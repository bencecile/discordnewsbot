# Discord News Bot
The basic premise of this bot is that it will take the lists from a Twitter account, and sends
the tweets from the accounts in list to a channel in Discord.

This means that you would require both a Twitter App and a Discord Bot, with both of the associated
API keys. This information then needs to be placed in a file called info.json.

## Format of info.json
```{json}
{
    "discord": {
        "botToken": "[contents of the token]"
    },
    "twitter": {
        "apiKey": "[api key]",
        "apiSecretKey": "[api secret key]",
        "accessToken": "[access token]",
        "accessTokenSecret": "[access token secret]"
    }
}
```
