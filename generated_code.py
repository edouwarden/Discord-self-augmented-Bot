async def main(client):
    @client.event
    async def on_message(message):
        # Ignore ourselves and our own webhook posts, otherwise the webhook
        # messages can trigger this handler again.
        if message.author == client.user or message.webhook_id is not None:
            return

        # Update the condition for long messages
        if len(message.content) > 250:
            await message.add_reaction("😴")

        # Check if the message contains "julien" and reply with the specified link
        if "julien" in message.content.lower():
            await message.channel.send("https://youtu.be/B3FQ9HUg1ZY?t=242")

        # Add a penguin reaction when this user replies
        if message.author.id == 937100762444619806:
            await message.add_reaction("🐧")

        # Send a private message to one specific user
        if message.author.id in [339389843195035649]:
            await message.author.send("nothing")
