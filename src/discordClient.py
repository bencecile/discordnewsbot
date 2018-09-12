import asyncio
from enum import IntEnum
import json
import time

import requests
import websockets

# Omitting the api version number will go to the default (latest?) version
BASE_URL = "https://discordapp.com/api"

class ChannelTypes(IntEnum):
    """
    This is the possible types of channel types
    """
    GUILD_TEXT = 0
    DM = 1
    GUILD_VOICE = 2
    GROUP_DM = 3
    GUILD_CATEGORY = 4

class Route:
    """
    The definition of our Route for our Routes
    """
    def __init__(self, endingPath):
        self.path = BASE_URL + endingPath

    def makeURL(self, formatID=None):
        """
        Formats the route with the ID if it isn't None
        """
        if formatID is None:
            return self.path
        # Only format when not-None so we don't get an error
        return self.path.format(formatID)

class Routes:
    """
    The different routes that can be accessed on Discord
    """
    # Channel routes
    CHANNEL_MESSAGES = Route("/channels/{}/messages")

    # Gateway routes
    GATEWAY_BOT = Route("/gateway/bot")

    # Guild Routes
    GUILD_CHANNELS = Route("/guilds/{}/channels")

    # User routes
    USER_ME_GUILDS = Route("/users/@me/guilds")

class DiscordClient:
    """
    This is the client that will wrap all of the calls made to Discord
    """
    def __init__(self, botToken):
        self.botToken = botToken
        self.session = requests.Session()
        # Update the authorization header that we will always use
        self.session.headers.update({"Authorization": f"Bot {botToken}"})
        # Keep track of all of the rate limits for each of the calls that we make
        # The channelID, guildID, or webhookID make each rate limit unique
        self.rateLimits = {}

    def initialize(self):
        """
        Initializes the client by making a connection to the websocket gateway, and setting their
        presence to online
        """
        gatewayBotInfo = self.getGatewayBot()
        self.websocket = Websocket(gatewayBotInfo["url"], self.botToken)
        asyncio.get_event_loop().run_until_complete(self.websocket.identify(Status.ONLINE))

    def goOffline(self):
        """
        Updates the presence of the bot to Offline
        Can only be called after initialize
        """
        asyncio.get_event_loop().run_until_complete(self.websocket.identify(Status.OFFLINE))

    def checkResponse(self, response):
        """
        Checks the Response, and returns the json if it's not a failure
        """
        # Only set the rate limit if they give us some
        if "X-RateLimit-Remaining" in response.headers:
            # Set the rate limits
            self.rateLimits[response.url] = {
                # The remaining number of requests to this url
                "remaining": int(response.headers["X-RateLimit-Remaining"]),
                # The time (seconds from the 1970 epoch) that this rate limit will reset
                "reset": int(response.headers["X-RateLimit-Reset"]),
            }
        response.raise_for_status()
        return response.json()

    def atRateLimit(self, url):
        """
        The URL can be created from a Route

        Returns True if we have hit the rate limit
        """
        if url in self.rateLimits:
            rateLimit = self.rateLimits[url]
            # We may still be affected by the rate limit depending on the time
            if rateLimit["remaining"] == 0:
                # We aren't rate limited if we have gone over the reset time
                if rateLimit["reset"] < time.time():
                    return False
                return True
        return False

    # All of the Channel endpoints
    def createChannelMessage(self, channelID, content):
        """
        Sends a message to the channel with the content.
        Returns the created message.

        POST /channels/{channel.id}/messages
        """
        payload = {
            "content": content
        }
        return self.checkResponse(
            self.session.post(Routes.CHANNEL_MESSAGES.makeURL(channelID), json=payload)
        )

    def getChannelMessages(self, channelID, count=None):
        """
        Returns a list of messages from the channel. If count is specified, it will get only that
        amount of messages

        GET /channels/{channel.id}/messages
        """
        query = None
        if count is not None:
            query = { "limit": count }

        return self.checkResponse(
            self.session.get(Routes.CHANNEL_MESSAGES.makeURL(channelID), params=query)
        )

    # All of the Gateway endpoints
    def getGatewayBot(self):
        """
        Returns the gateway information for a bot

        GET /gateway/bot
        """
        return self.checkResponse( self.session.get(Routes.GATEWAY_BOT.makeURL()) )

    # All of the Guild endpoints
    def createGuildChannel(self, guildID, name, channelType, parentID=None):
        """
        Creates a new channel in the guild. Returns the newly created channel

        POST /guilds/{guild.id}/channels
        """
        payload = {
            "name": name,
            "type": channelType,
        }
        # Only set the parent ID if we have one
        if parentID is not None:
            payload["parent_id"] = parentID
        return self.checkResponse(
            self.session.post(Routes.GUILD_CHANNELS.makeURL(guildID), json=payload)
        )

    def getGuildChannels(self, guildID):
        """
        Returns all of the guild's channels, where the guild is determined by the ID

        GET /guilds/{guild.id}/channels
        """
        return self.checkResponse( self.session.get(Routes.GUILD_CHANNELS.makeURL(guildID)) )

    def modifyGuildChannelPositions(self, guildID, positionPairs):
        """
        Modifies the positions of the channels in the guild. Takes a list of position pairs.
        Each position pair should be a tuple: (channel_id, position)

        PATCH /guilds/{guild.id}/channels
        """
        # Do nothing if we don't have any position pairs
        if len(positionPairs) == 0:
            return

        payload = [{ "id": pair[0], "position": pair[1] } for pair in positionPairs]
        self.session.patch(Routes.GUILD_CHANNELS.makeURL(guildID), json=payload).raise_for_status()

    # All of the User endpoints
    def getMyGuilds(self):
        """
        Gets all of this bot's guilds

        GET /users/@me/guilds
        """
        return self.checkResponse( self.session.get(Routes.USER_ME_GUILDS.makeURL()) )

def makeWSPayload(opcode, data):
    """
    Returns a json payload to use for a websocket message
    """
    return json.dumps({
        "op": opcode,
        "d": data,
    })

class Status:
    """
    These are the different statuses that can be set with the presence in identify
    """
    ONLINE = "online"
    DO_NOT_DISTURB = "dnd"
    IDLE = "idle"
    INVISIBLE = "invisible"
    OFFLINE = "offline"

class Websocket:
    """
    Holds the information required to run the websocket commands.
    
    Does this all synchronously for simplicity
    """
    def __init__(self, url, botToken):
        """
        Determines whether or not to use SSL from the given url
        """
        self.ssl = url.startswith("wss://")
        # Add the URL parameters that we will use when connection (version 6 and json transport)
        self.url = f"{url}?v=6&encoding=json"
        self.botToken = botToken

    async def identify(self, status):
        """
        Goes through the entire identify workflow, updating the presence with the status
        """
        async with websockets.connect(self.url, ssl=self.ssl) as ws:
            # We should be getting a "hello" dispatch immediately
            # NOTE Do nothing with this since we won't be connected long enough to need a heartbeat
            await ws.recv()

            # Send the actual identify payload
            await ws.send(makeWSPayload(2, {
                "token": self.botToken,
                "properties": {
                    # Don't think the actual value of this matters very much
                    "$os": "Windows",
                    "$browser": "BotForNews",
                    "$device": "BotForNews",
                },
                "presence": {
                    "status": status,
                },
            }))

            # Wait for the ready event that they will send back
            # NOTE Do nothing the ready event
            await ws.recv()
