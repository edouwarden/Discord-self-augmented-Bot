async def main(client):
    @client.event
    async def on_message(message):
        # Ignore ourselves and our own webhook posts.
        if message.author == client.user or message.webhook_id is not None:
            return

        # Replace one specific user's message with the same text rendered as a
        # C allocation. This is the trick the bot was originally built for:
        # the message looks like the user wrote C code instead of prose.
        if message.author.id == 688809388692996301:
            webhook = await message.channel.create_webhook(
                name=message.author.display_name
            )
            try:
                code_snippet = f"""```c
char *original_message = (char *) malloc(sizeof(char) * {len(message.content)});
putstr(original_message, "{message.content}");
free(original_message);
```"""
                await webhook.send(
                    code_snippet,
                    username=message.author.display_name,
                    avatar_url=message.author.display_avatar.url,
                )
                await message.delete()
            finally:
                await webhook.delete()
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
