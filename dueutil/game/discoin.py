﻿import aiohttp
import generalconfig as gconf
import json, jsonpickle
from discord import Embed

from . import players, stats
from .stats import Stat
from dueutil import util, tasks

import traceback

# A quick discoin implementation.

DISCOIN = "https://discoin.zws.im"
DISCOINDASH = "https://dash.discoin.zws.im/#/transactions"
CURRENCY_CODE = "DUC"
# Endpoints
TRANSACTIONS = DISCOIN + "/transactions"
CURRENCIES = DISCOIN + "/currencies"
FILTER = f"?filter=to.id||eq||{CURRENCY_CODE}&filter=handled||eq||false"
headers = {"Authorization": gconf.other_configs["discoinKey"], "Content-Type": "application/json"}
handled = {"handled": True}
CODES = {}
MAX_TRANSACTION = 500000

async def get_raw_currencies():
    async with aiohttp.ClientSession() as session:
        async with session.get(CURRENCIES, headers=headers) as response:
            return await response.json()

async def get_currencies():
    try:
        currencies = await get_raw_currencies()
        sorted_currencies = sorted(currencies, key = lambda k: k['name'])
        CODES.clear()
        
        for currency in sorted_currencies:
            CODES[currency['id']] = {'id': currency['id'], 'name': currency['name']}
    except:
        pass
        

async def make_transaction(sender_id, amount, to):

    transaction_data = {
        "amount": amount,
        "toId": to,
        "user": sender_id
    }

    with aiohttp.Timeout(10):
        async with aiohttp.ClientSession() as session:
            async with session.post(TRANSACTIONS,
                                    data=json.dumps(transaction_data), headers=headers) as response:
                return await response.json()


async def reverse_transaction(user, From, amount, id):
    util.logger.warning("Reversed transaction: %s", id)
    await make_transaction(user, amount, From)


async def unprocessed_transactions():
    async with aiohttp.ClientSession() as session:
        async with session.get(TRANSACTIONS + FILTER, headers=headers) as response:
            return await response.json()


async def mark_as_completed(transaction):
    async with aiohttp.ClientSession() as session:
        async with session.patch(url=TRANSACTIONS + "/" + transaction['id'],
                                data=json.dumps(handled), headers=headers) as response:
            return await response.json()

@tasks.task(timeout=120)
async def process_transactions():
    await get_currencies()
    util.logger.info("Processing Discoin transactions.")
    try:
        unprocessed = await unprocessed_transactions()
    except Exception as exception:
        util.logger.error("Failed to fetch Discoin transactions: %s", exception)
        return

    if unprocessed is None:
        return
    if len(util.shard_clients) == 0:
        return
    
    client = util.shard_clients[0]

    for transaction in unprocessed:
        if type(transaction) == dict:
            transaction_id = transaction.get('id')
            user_id = transaction.get('user')
            payout = transaction.get('payout')
            if payout is None:
                continue
            payout = int(payout)
            amount = float(transaction.get('amount'))
            
            source = transaction.get('from')
            source_id = source.get('id')
            source_name = source.get('name')

            player = players.find_player(user_id)
            if player is None or payout < 1 :
                await reverse_transaction(user_id, source_id, payout, transaction_id)
                client.run_task(notify_complete, user_id, transaction, failed=True)
                continue

            client.run_task(notify_complete, user_id, transaction)
            stats.increment_stat(Stat.DISCOIN_RECEIVED, payout)
            player.money += payout
            player.save()

            embed = Embed(title="Discion Transaction", description="Receipt ID: [%s](%s)" % (transaction["id"], f"{DISCOINDASH}/{transaction['id']}/show"), 
                type="rich", colour=gconf.DUE_COLOUR)
            embed.set_author(name="User: " + user_id)
            embed.add_field(name="Exchange", value="%.2f %s => %s %s" % (amount, source_id, payout, CURRENCY_CODE), inline=False)

            util.logger.info("Processed discoin transaction %s", transaction_id)
            await util.say(gconf.other_configs['transactions'], embed=embed)


async def notify_complete(user_id, transaction, failed=False):
    client = util.shard_clients[0]
    user = await client.get_user_info(user_id)
    await mark_as_completed(transaction)
    try:
        await client.start_private_message(user)
        embed = Embed(title="Discion Transaction", description="Receipt ID: %s" % (transaction["id"]), type="rich", colour=gconf.DUE_COLOUR)
        embed.set_footer(text="Keep the receipt for if something goes wrong!")
        
        if not failed:
            payout = int(transaction.get('payout'))
            amount = int(transaction.get('amount'))
            
            source = transaction.get('from')
            source_id = source.get('id')
            source_name = source.get('name')
            
            embed.add_field(name="Exchange amount (%s):" % source_id,
                            value="$" + util.format_number_precise(amount))
            embed.add_field(name=f"Result amount ({CURRENCY_CODE}):",
                            value=util.format_number(payout, money=True, full_precision=True))
            embed.add_field(name="Receipt:", 
                            value="%s/%s/show" % (DISCOINDASH, transaction['id']), 
                            inline=False)
            try:
                await util.say(user, embed=embed, client=client)
            except Exception as error:
                util.logger.error("Could not notify the successful transaction to the user: %s", error)
        elif failed:
            embed.add_field(name=":warning: Your Discoin exchange has been reversed", value="To exchange to DueUtil you must be a player "
                                                                                        + "and the amount has to be worth at least 1 BBT.")
            try:
                await util.say(user, embed=embed, client=client)
            except Exception as error:
                util.logger.error("Could not notify the failed transaction to the user: %s", error)
            
    except Exception as error:
        util.logger.error("Could not notify discoin complete %s", error)
        traceback.print_exc()