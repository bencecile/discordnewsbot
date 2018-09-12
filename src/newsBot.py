import asyncio
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import sched
import time

import twitter

from discordClient import ChannelTypes, DiscordClient, Routes

# This is the cache folder that we can use for storing results and stuff
CACHE = Path("cache")

# The path to the info file
INFO_FILE = Path("info.json")

# This is the format that we will use for our messages
# With the magic of Discord, the twitter link will be pulled and parsed
MESSAGE_FORMAT = """URL: https://twitter.com/{screenName}/status/{id}
Date: {date}{retweetStatus}"""

DISCORD_MESSAGE_RE = re.compile(r"(\d+)$", re.M)
def getIDFromMessage(message):
    """
    Extracts the Twitter ID from the message
    Returns that ID or None, if the message doesn't contain a message
    """
    idMatch = DISCORD_MESSAGE_RE.search(message)
    if idMatch is not None:
        return int(idMatch[1])

    return None

class TwitterUpdater:
    def __init__(self, scheduler, discordClient, twitterClient):
        self.scheduler = scheduler
        self.discordClient = discordClient
        self.twitterClient = twitterClient
        # Keep track of the status of all our lists
        self.twitterLists = {}
        # The name for our Discord channel category
        self.categoryName = None
        # This will be set if we have an overflow of messages that we need to send
        self.sendingMessages = False

    def setup(self):
        """
        Do some first time setup. Should be called only once
        """
        twitterUser = self.twitterClient.VerifyCredentials()
        self.updateTwitterLists()
        # This is the channel category name that we will use to create twitter account channels
        self.categoryName = f"Twitter For {twitterUser.screen_name}"

        # Get all of this bot's guilds
        # NOTE This assumes that this bot is only a part of a single guild at a time
        guild = self.discordClient.getMyGuilds()[0]
        self.guildID = guild["id"]

    def updateTwitterLists(self):
        """
        This will update the lists that we have created

        Shouldn't be called from outside the class
        """
        lists = self.twitterClient.GetLists()
        for twitterList in lists:
            if twitterList.name not in self.twitterLists:
                self.twitterLists[twitterList.name] = {
                    # The Twitter id of the list
                    "id": twitterList.id,
                    # The ID of the discord channel
                    "channelID": None,
                    # These are all of the messages that need to be sent to Discord
                    "messages": [],
                }

    def channelMaintenance(self):
        """
        Checks all of the channels for any irregularities
        Creates any channels that are missing (includes the channel category)
        """
        # Create the channel category if it doesn't exist
        channelCategory = None
        currentChannels = self.discordClient.getGuildChannels(self.guildID)
        for channel in currentChannels:
            if channel["type"] == ChannelTypes.GUILD_CATEGORY and channel["name"] == self.categoryName:
                # Looks like we found the channel category
                channelCategory = channel

        if channelCategory is None:
            # Now we have to create the channel category
            channelCategory = self.discordClient.createGuildChannel(self.guildID, self.categoryName,
                ChannelTypes.GUILD_CATEGORY)

        # Rip out the channels that aren't underneath our channel category
        currentChannels = [channel for channel in currentChannels if channel["parent_id"] == channelCategory["id"]]

        # Create any missing Discord channels (the @name (screen_name) for the Twitter accounts)
        # If it does exist, read the lastest message from it
        for listName in self.twitterLists:
            twitterList = self.twitterLists[listName]
            foundChannel = False

            # Only check for the ID if we don't have it
            if twitterList["channelID"] is None:
                for channel in currentChannels:
                    # Chech if we have the correct child category and the name matches
                    if channel["parent_id"] == channelCategory["id"] and channel["name"] == listName.lower():
                        # We have found the correct channel so we can also set it in our friends
                        foundChannel = True
                        twitterList["channelID"] = channel["id"]
                        break
            else:
                foundChannel = True
            
            # Create the missing channel now, and add it to our list so that it be up to date
            if not foundChannel:
                # Make sure we're not at the rate limit when creating all these channels
                # Sleep for 10 seconds before we check the rate limit again
                # This is needed for Heroku because it doesn't like small sleep times
                while self.discordClient.atRateLimit(Routes.GUILD_CHANNELS.makeURL(self.guildID)):
                    time.sleep(10)
                currentChannels.append(self.discordClient.createGuildChannel(self.guildID,
                    listName, ChannelTypes.GUILD_TEXT, channelCategory["id"]))

                # Add the channel id to the friend dictionary from the one we just created
                twitterList["channelID"] = currentChannels[-1]["id"]

        # Make sure that all of the channels are in alphabetical order, first
        nameSort = lambda channel: channel["name"]
        oldChannels = currentChannels.copy()
        currentChannels.sort(key=nameSort)

        # Update the channels' positions, only if they actually need to change
        positionPairs = [(channel["id"], i) for (i, channel) in enumerate(currentChannels) if channel != oldChannels[i]]
        # This will won't do anything if we didn't find any pairs
        self.discordClient.modifyGuildChannelPositions(self.guildID, positionPairs)

    def checkMessageRateLimit(self, channelID):
        """
        Checks the rate limit for the messages for the channel
        Will schedule another sending messages if we are at the limit

        Returns True if we have hit the rate limit
        """
        if self.discordClient.atRateLimit(Routes.CHANNEL_MESSAGES.makeURL(channelID)):
            # We will have to wait at 10 second intervals because the Heroku system doesn't do well
            #  with quick sleeps
            self.scheduler.enter(10, 0, self.sendMessages)
            return True

        return False

    def sendMessages(self):
        """
        Should only be called if messages are being sent
        Essentially sends all of the messages that are waiting for each friend

        If the limit for sending messages has been reached, we will schedule time to try again
        """
        # Always set this to start so that it can't be called again
        self.sendingMessages = True

        # Find any messages that we need to send
        for listName in self.twitterLists:
            twitterList = self.twitterLists[listName]
            channelID = twitterList["channelID"]

            # Copy the messages so that we can manipulate the list on the friend and be able to
            #  resume and message sending
            copiedMessages = twitterList["messages"].copy()
            for message in copiedMessages:
                # Only go proceed if are not at the rate limit
                if not self.checkMessageRateLimit(channelID):
                    # Send the message to the channel
                    self.discordClient.createChannelMessage(channelID, message)
                    # Always pop the first one and the rest will get shifted
                    twitterList["messages"].pop(0)
                else:
                    # We will need to return for now and wait until we get scheduled again
                    return

        # We are now done sending all of the messages
        self.sendingMessages = False

    def doUpdate(self):
        # Check that we've been setup
        if self.categoryName is None:
            raise RuntimeError("We haven't been setup yet. Check it out because it's a bug.")

        # Log the current time
        logging.info(f"Doing a status update at {datetime.today()}")

        # Fetch the list of Twitter accounts to look at
        # We need to do this everytime just in case it got updated recently
        self.updateTwitterLists()
        
        # Channels may have changed since the last update
        self.channelMaintenance()
        
        # Fetch the news feeds for the twitter accounts
        # Get the last 20 tweets if there isnt't any progress in Discord yet
        for listName in self.twitterLists:
            twitterList = self.twitterLists[listName]
            lastPostID = None

            # Don't do any fetching from Discord if we still have messages that we need to send
            if len(twitterList["messages"]) > 0:
                # Instead we can check our last message for an ID to check
                lastPostID = getIDFromMessage(twitterList["messages"][-1])
            else:
                # Make sure that we are under the rate limit before we check any messages
                if self.checkMessageRateLimit(twitterList["channelID"]):
                    # Just continue because we can't know the last post right now
                    continue
                # Check the latest post in each channel to get the latest tweet id
                messages = self.discordClient.getChannelMessages(twitterList["channelID"], 1)
                # Make sure we at least have a message
                if len(messages) > 0:
                    lastPostID = getIDFromMessage(messages[0]["content"])
            
            posts = []
            if lastPostID is None:
                # Get the latest 100 here since we don't have any posts
                posts = self.twitterClient.GetListTimeline(
                    list_id=twitterList["id"],
                    count=100,
                    # We don't want extra metadata that we won't use
                    include_entities=False,
                )
            else:
                # Get all of the posts up to the latest one
                posts = self.twitterClient.GetListTimeline(
                    list_id=twitterList["id"],
                    since_id=lastPostID,
                    # We don't want extra metadata that we won't use
                    include_entities=False,
                )

            # Reverse all of the posts since the fetching makes the most recent be first
            posts.reverse()

            # Format and add all of the posts to our messages
            for post in posts:
                messageKeywords = {
                    "id": post.id,
                    "date": post.created_at,
                    "screenName": post.user.screen_name,
                    "retweetStatus": "\n<Retweet>" if post.retweeted_status else "",
                }
                twitterList["messages"].append(MESSAGE_FORMAT.format(**messageKeywords))

        # Send all of the messages only if we don't have any messages queued
        if not self.sendingMessages:
            self.sendMessages()

        # Find out the next Twitter API usage reset (15 minute intervals)
        nextResetTime = self.twitterClient.CheckRateLimit("/lists/statuses.json").reset

        # Schedule that time for our next update
        # Wait for an extra second just to be safe
        self.scheduler.enterabs(nextResetTime + 1, 0, self.doUpdate)

        # Log the finishing time too
        logging.info(f"Finished a status update at {datetime.today()}")
        
def main():
    # Set up logging
    logging.basicConfig(level=logging.INFO)

    if not INFO_FILE.exists():
        # Check the config variables before we fail
        if "info.json" in os.environ:
            info = json.loads(os.environ["info.json"])
        else:
            raise RuntimeError(f"{INFO_FILE} must exist, or the config var must be set")
    else:
        # Read in the info that we need
        with INFO_FILE.open(encoding="UTF-8") as infoFile:
            info = json.load(infoFile)

    discordInfo = info["discord"]
    twitterInfo = info["twitter"]

    # Log into Discord
    discordClient = DiscordClient(discordInfo["botToken"])
    discordClient.initialize()

    # Log into Twitter
    twitterClient = twitter.Api(
        consumer_key=twitterInfo["apiKey"],
        consumer_secret=twitterInfo["apiSecretKey"],
        access_token_key=twitterInfo["accessToken"],
        access_token_secret=twitterInfo["accessTokenSecret"],
    )

    # Create our own scheduler
    # Use time.time since we need for it to be using the same GMT epoch counting method as
    #  the Twitter rate limit timing
    scheduler = sched.scheduler(timefunc=time.time)

    # Create the updater that we will use for Twitter
    twitterUpdater = TwitterUpdater(scheduler, discordClient, twitterClient)
    twitterUpdater.setup()

    # Add a twitter update to do right once we start running the scheduler
    scheduler.enter(0, 0, twitterUpdater.doUpdate)
    
    try:
        # Run the scheduler forever
        scheduler.run()
    except KeyboardInterrupt:
        # Just exit if we get interrupted
        logging.info("Interrupted")

    # Log out from any of the clients
    discordClient.goOffline()
    logging.info("Finished")

if __name__ == "__main__":
    main()
