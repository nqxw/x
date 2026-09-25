# modifyself

A clean, pythonic Discord self-bot library.

## Installation

pip install modifyself

## Quick Example

rom modifyself import Client, command

bot = Client(token="your_token")

@bot.command()
async def ping(ctx):
    await ctx.reply("Pong!")

bot.run()

## Features

- Clean, intuitive API
- Command framework with cogs
- Automatic rate limiting
- Gateway event handling
- Full API coverage

## License

MIT
