import asyncio
import inspect
import os
import queue
import re
import traceback
from threading import Thread
from multiprocessing import Process, dummy
import aiohttp
import gc
import time
start_time = time.time()
time_shown = False
import sys
from itertools import cycle
import sentry_sdk
sentry_sdk.init("https://5322a6d1b40841d7a6000e45a3c61a03@sentry.io/1406854")

import discord
from dueutil.permissions import Permission

import generalconfig as gconf
from dueutil import loader, servercounts
from dueutil.game import players, stats, emojis
from dueutil.game.stats import Stat
from dueutil.game.helpers import imagecache
from dueutil.game.configs import dueserverconfig
from dueutil import permissions
from dueutil import util, events, dbconn

MAX_RECOVERY_ATTEMPTS = 1000

stopped = False
bot_key = ""
shard_count = 0
client = None
clients = []
shard_names = []

# I'm not sure of the root cause of this error & it only happens once in months.
ERROR_OF_DEATH = "Timeout context manager should be used inside a task"

""" 
DueUtil: The most 1337 (worst) discord bot ever.     
This bot is not well structured...
(c) MacDue & DeveloperAnonymous - All rights reserved
(Sections of this bot are MIT and GPL)
"""

class DueUtilClient(discord.AutoShardedClient):
    """
    DueUtil shard client
    """

    def __init__(self, **details):
        self.queue_tasks = queue.Queue()
        self.start_time = time.time()
        super(DueUtilClient, self).__init__(**details)
        asyncio.ensure_future(self.__check_task_queue(), loop=self.loop)

    
    async def __check_task_queue(self):
        while True:
            try:
                task_details = self.queue_tasks.get(False)
                task = task_details["task"]
                args = task_details.get('args', ())
                kwargs = task_details.get('kwargs', {})
                if inspect.iscoroutinefunction(task):
                    await task(*args, **kwargs)
                else:
                    task(args, kwargs)
            except queue.Empty:
                pass
            await asyncio.sleep(0.1)

    def run_task(self, task, *args, **kwargs):
        """
        Runs a task from within this clients thread
        """
        self.queue_tasks.put({"task": task, "args": args, "kwargs": kwargs})

    
    async def on_guild_join(self, guild):
        server_count = util.get_server_count()
        if server_count % 100 == 0:
            await util.say(gconf.announcement_channel,
                                ":confetti_ball: I'm on __**%d SERVERS**__ now!1!111!\n@everyone" % server_count)

        util.logger.info("Joined guild name: %s id: %s", guild.name, guild.id)
        await util.set_up_roles(guild)
        server_stats = self.server_stats(guild)
        await util.duelogger.info(("BattleBanana has joined the guild **"
                                        + util.ultra_escape_string(guild.name) + "**!\n"
                                        + "``Member count →`` " + str(server_stats["member_count"]) + "\n"
                                        + "``Bot members →``" + str(server_stats["bot_count"]) + "\n"
                                        + ("**BOT SERVER**" if server_stats["bot_server"] else "")))

        # Message to help out new guild admins.
        for channel in guild.channels:
            if channel.type == discord.ChannelType.text:
                try:
                    await channel.send(":wave: __Thanks for adding me!__\n"
                                     + "If you would like to customize me to fit your "
                                     + "guild take a quick look at the admins "
                                     + "guide at <https://dueutil.xyz/howto/#adming>.\n"
                                     + "It shows how to change the command prefix here, and set which "
                                     + "channels I or my commands can be used in (along with a bunch of other stuff).")
                    break
                except discord.Forbidden:
                    continue
        
        # Update stats
        await servercounts.update_server_count(self)

    @staticmethod
    def server_stats(guild):
        member_count = len(guild.members)
        bot_count = sum(member.bot for member in guild.members)
        bot_percent = int((bot_count / member_count) * 100)
        bot_server = bot_percent > 70
        return {"member_count": member_count, "bot_percent": bot_percent,
                "bot_count": bot_count, "bot_server": bot_server}

    
    async def on_error(self, event, *args):
        ctx = args[0] if len(args) == 1 else None
        ctx_is_message = isinstance(ctx, discord.Message)
        error = sys.exc_info()[1]
        if ctx is None:
            await util.duelogger.error(("**BattleBanana experienced an error!**\n"
                                             + "__Stack trace:__ ```" + traceback.format_exc() + "```"))
            util.logger.error("None message/command error: %s", error)
        elif isinstance(error, util.DueUtilException):
            # A normal dueutil user error
            try:
                if error.channel is not None:
                    await util.say(error.channel, error.get_message())
                else:
                    await util.say(ctx.channel, error.get_message())
            except:
                util.logger.warning("Unable to send Exception message")
            return
        elif isinstance(error, util.DueReloadException):
            loader.reload_modules()
            await util.say(error.channel, loader.get_loaded_modules())
            return
        elif isinstance(error, discord.errors.Forbidden):
            if ctx_is_message:
                channel = ctx.channel
                if isinstance(error, util.SendMessagePermMissing):
                    util.logger.warning("Missing send permissions in channel %s (%s)", channel.name, channel.id)
                else:
                    try:
                        # Attempt to warn user
                        perms = ctx.guild.me.permissions_in(ctx.channel)
                        await util.say(ctx.channel,
                                            "The action could not be performed as I'm **missing permissions**! Make sure I have the following permissions:\n"
                                            + "- Manage Roles %s;\n" % (":white_check_mark:" if perms.manage_roles else ":x:")
                                            + "- Manage messages %s;\n" % (":white_check_mark:" if perms.manage_messages else ":x:")
                                            + "- Embed links %s;\n" % (":white_check_mark:" if perms.embed_links else ":x:")
                                            + "- Attach files %s;\n" % (":white_check_mark:" if perms.attach_files else ":x:")
                                            + "- Read Message History %s;\n" % (":white_check_mark:" if perms.read_message_history else ":x:")
                                            + "- Use external emojis %s;\n" % (":white_check_mark:" if perms.external_emojis else ":x:")
                                            + "- Add reactions%s" % (":white_check_mark:" if perms.add_reactions else ":x:")
                                            )
                    except util.SendMessagePermMissing:
                        pass  # They've block sending messages too.
                    except discord.errors.Forbidden: 
                        pass
                return
        elif isinstance(error, discord.HTTPException):
            util.logger.error("Discord HTTP error: %s", error)
        elif isinstance(error, aiohttp.ClientResponseError):
            if ctx_is_message:
                util.logger.error("%s: ctx from %s: %s", error, ctx.author.id, ctx.content)
            else:
                util.logger.error(error)
        elif isinstance(error, RuntimeError) and ERROR_OF_DEATH in str(error):
            util.logger.critical("Something went very wrong and the error of death came for us: %s", error)
            os._exit(1)
        elif ctx_is_message:
            await util.say(ctx.channel, (":bangbang: **Something went wrong...**"))
            trigger_message = discord.Embed(title="Trigger", type="rich", color=gconf.DUE_COLOUR)
            trigger_message.add_field(name="Message", value=ctx.author.mention + ":\n" + ctx.content)
            await util.duelogger.error(("**Message/command triggred error!**\n"
                                             + "__Stack trace:__ ```" + traceback.format_exc()[-1500:] + "```"),
                                            embed=trigger_message)
        # Log exception on sentry.
        util.sentry_client.captureException()
        traceback.print_exc()

    
    async def on_message(self, message):
        if (message.author == self.user
            or message.author.bot
            or isinstance(message.channel, discord.abc.PrivateChannel)):
            return

        owner = message.author
        if owner.id == config["owner"] and not permissions.has_permission(owner, Permission.DUEUTIL_OWNER):
            permissions.give_permission(owner, Permission.DUEUTIL_OWNER)

        mentions_self_regex = f"<@.?{self.user.id}>"
        if re.match("^"+mentions_self_regex, message.content):
            message.content = re.sub(mentions_self_regex + "\s*",
                                    dueserverconfig.server_cmd_key(message.guild),
                                    message.content)
            
        await events.on_message_event(message)

    
    async def on_member_update(self, before, after):
        player = players.find_player(before.id)
        if player is not None:
            old_image = player.get_avatar_url(member=before)
            new_image = player.get_avatar_url(member=after)
            if old_image != new_image:
                imagecache.uncache(old_image)
            member = after
            if (member.guild.id == gconf.THE_DEN and any(role.id == gconf.DONOR_ROLE_ID for role in member.roles)):
                player.donor = True
                player.save()

    
    async def on_guild_remove(self, guild):
        for collection in dbconn.db.collection_names():
            if collection != "Player":
                dbconn.db[collection].delete_many({'_id': {'$regex': '%s.*' % guild.id}})
                dbconn.db[collection].delete_many({'_id': guild.id})
        await util.duelogger.info("BattleBanana has been removed from the guild **%s** (%s members)"
                                       % (util.ultra_escape_string(guild.name), guild.member_count))
        # Update stats
        await servercounts.update_server_count(self)

    
    async def change_avatar(self, channel, avatar_name):
        try:
            avatar = open("avatars/" + avatar_name.strip(), "rb")
            avatar_object = avatar.read()
            await self.edit(avatar=avatar_object)
            await util.say(channel, ":white_check_mark: Avatar now **" + avatar_name + "**!")
        except FileNotFoundError:
            await util.say(channel, ":bangbang: **Avatar change failed!**")

    
    async def on_ready(self):
        game = discord.Activity(name="!help | dueutil.xyz", type=discord.ActivityType.watching)
        try:
            await self.change_presence(activity=game)
        except Exception as e:
            util.logger.error("Failed to change presence")
            
        util.logger.info("Bot started after %.2fs & Shards started after %.2fs", time.time() - start_time, time.time() - client.start_time)
        await util.duelogger.bot("DueUtil has *(re)*started\n"
                                        + "Bot version → ``%s``" % gconf.VERSION)

    
    async def on_shard_ready(self, shard_number):
        # game = discord.Activity(name="dueutil.xyz | shard %d/%d" % (shard_number+1, shard_count))
        # try:
        #     await self.change_presence(activity=game)
        # except Exception as e:
        #     util.logger.error("Failed to change presence")

        util.logger.info("\nLogged in shard %d as\n%s\nWith account @%s ID:%s \n-------",
                         shard_number + 1, shard_names[shard_number], self.user.name, self.user.id)



class ClientThread(dummy.Process):
    """
    Thread for a client
    """
    def __init__(self, event_loop):
        self.event_loop = event_loop
        super().__init__()

    def run(self, level=1):
        asyncio.set_event_loop(self.event_loop)
        global client
        client = DueUtilClient(shard_count=shard_count, fetch_offline_members=False)
        clients.append(client)
        try:
            asyncio.run_coroutine_threadsafe(client.run(bot_key), client.loop)
        except Exception as client_exception:
            util.logger.exception(client_exception, exc_info=True)
            if level < MAX_RECOVERY_ATTEMPTS:
                util.logger.warning("Bot recovery attempted")
                self.event_loop = asyncio.new_event_loop()
                self.run(level + 1)
            else:
                util.logger.critical("FATAL ERROR: Recovery failed")
        finally:
            util.logger.critical("Bot is down & needs restarting!")
            # Should restart bot
            os._exit(1)


def run_due():
    if not os.path.exists("assets/imagecache/"):
        os.makedirs("assets/imagecache/")
    loader.load_modules(packages=loader.GAME)
    if not stopped:
        loader.load_modules(packages=loader.COMMANDS)

        util.logger.info("Modules loaded after %.2fs", time.time() - start_time)
        shard_time = time.time()

        client_thread = ClientThread(asyncio.new_event_loop())
        client_thread.start()

        while not len(clients) == 1:
            pass
        while not client.is_ready():
            pass
        
        # TODO: Show the time it takes to turn on the bot & time it took to start shards
        util.logger.info("Bot started after %.2fs & Shards started after %.2fs", time.time() - start_time, time.time() - shard_time)

        ### Tasks
        loop = asyncio.get_event_loop()
        from dueutil import tasks
        for task in tasks.tasks:
            asyncio.ensure_future(task(), loop=loop)
        loop.run_forever()

if __name__ == "__main__":
    util.logger.info("Starting BattleBanana!")
    config = gconf.other_configs
    bot_key = config["botToken"]
    shard_count = config["shardCount"]
    shard_names = config["shardNames"]
    util.load(clients)
    run_due()
