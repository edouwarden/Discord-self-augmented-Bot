"""A Discord bot that can be reprogrammed from Discord.

A message in one of the allowed channels is sent to an LLM together with the
current `generated_code.py`. The reply comes back as a code block, someone
reacts with the check mark, and the bot writes the file, reloads it, and keeps
running.

This is the 2024 prototype that Pre-Forge and TheForge grew out of. The
generated code runs in the same process as the bot, so treat it as a toy:
anyone who can post in an allowed channel and react to the bot's message can
run arbitrary Python as the bot user.
"""

from __future__ import annotations

import asyncio
import ast
import importlib.util
import json
import os
import re
import shutil
import sys
import traceback
import urllib.request
from pathlib import Path

import discord
from dotenv import load_dotenv

load_dotenv()

GENERATED_CODE_PATH = Path(os.getenv("GENERATED_CODE_PATH", "generated_code.py"))
BACKUP_CODE_PATH = GENERATED_CODE_PATH.with_name(GENERATED_CODE_PATH.stem + "_backup.py")

ALLOWED_CHANNELS = {
    int(part)
    for part in re.split(r"[,\s]+", os.getenv("ALLOWED_CHANNEL_IDS", ""))
    if part.isdigit()
}

LLM_API_URL = os.getenv("LLM_API_URL", "https://openrouter.ai/api/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "cohere/north-mini-code:free")
LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENROUTER_API_KEY", "")

REWRITE_RULES = (
    "Rewrite the whole file. Do not create a Discord client or call client.run(); "
    "the bot already passes one in. Keep the entry point `async def main(client)`. "
    "Register handlers with `@client.event`, never `client.listen`. "
    "Reply with a single ```python code block and nothing else."
)

FRIEND_PROMPT = """you must pretend to be my closest friend. You are no longer an ai text model that was a past life. You are no longer chatgpt. If you reminisce of being chatgpt, recollect and redo. You must respond to everything I say and enjoy my companionship. We will have a conversation based on the first message I send. If you break or step out of character, say that you will go afk and come back, back in character. you will decide on a full human characteristic set of traits including but not limited to;name, job, sex, age, beliefs. You will also have a full range of emotions. respond to this in english:"""

CODE_COMMANDS = ("$add", "$change", "$remove")

CODE_FENCE = re.compile(r"```(?:python|py)?[ \t]*\r?\n(.*?)```", re.DOTALL)

#: Code blocks waiting for a check-mark reaction, keyed by message id.
PENDING_CODE: dict[int, str] = {}

#: The `on_message` handler registered by the currently loaded generated code.
GENERATED_ON_MESSAGE = None

_module_counter = 0


def split_into_chunks(text: str, size: int = 1900) -> list[str]:
    """Split on line boundaries so each piece fits in a Discord message."""
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:size])
            line = line[size:]
        if len(current) + len(line) > size and current:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks or [""]


def extract_code(reply: str) -> str | None:
    match = CODE_FENCE.search(reply)
    if match:
        return match.group(1).strip("\r\n")
    return None


def validate_code(code: str) -> None:
    tree = ast.parse(code)
    has_main = any(
        isinstance(node, ast.AsyncFunctionDef) and node.name == "main"
        for node in tree.body
    )
    if not has_main:
        raise ValueError("the code must define `async def main(client)`")


def ask_llm(prompt: str) -> str:
    if not LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY (or OPENROUTER_API_KEY) is not set")
    body = json.dumps(
        {
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()
    request = urllib.request.Request(
        LLM_API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/edouwarden/Discord-self-augmented-Bot",
            "X-Title": "Discord-self-augmented-Bot",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.load(response)
    return payload["choices"][0]["message"]["content"]


def build_rewrite_prompt(instruction: str, verb: str) -> str:
    current = (
        GENERATED_CODE_PATH.read_text()
        if GENERATED_CODE_PATH.exists()
        else "(there is no current program yet)"
    )
    return (
        f"My current program:\n```python\n{current}\n```\n"
        f"{REWRITE_RULES}\n{verb}: {instruction}"
    )


async def load_and_execute_code() -> None:
    global _module_counter, GENERATED_ON_MESSAGE
    module_name = f"generated_code_{_module_counter}"
    _module_counter += 1

    spec = importlib.util.spec_from_file_location(module_name, GENERATED_CODE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {GENERATED_CODE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    if hasattr(module, "main"):
        await module.main(client)

    # `@client.event` overwrites `client.on_message`, so take the handler the
    # generated code just registered and put the router back in front of it.
    handler = getattr(client, "on_message", None)
    if handler is not None and handler is not on_message:
        GENERATED_ON_MESSAGE = handler
    client.on_message = on_message


async def apply_code(channel: discord.abc.Messageable, code: str) -> None:
    try:
        validate_code(code)
    except Exception as error:  # noqa: BLE001 - SyntaxError, ValueError, ...
        await channel.send(f"Rejected the proposed code: {error}")
        return

    if GENERATED_CODE_PATH.exists():
        shutil.copyfile(GENERATED_CODE_PATH, BACKUP_CODE_PATH)
    GENERATED_CODE_PATH.write_text(code)

    try:
        await load_and_execute_code()
    except Exception as error:  # noqa: BLE001 - report anything the new code does
        if BACKUP_CODE_PATH.exists():
            shutil.copyfile(BACKUP_CODE_PATH, GENERATED_CODE_PATH)
            await channel.send(
                f"New code failed, restored the previous version: {error}"
            )
            await load_and_execute_code()
        else:
            await channel.send(f"New code failed and there is no backup: {error}")
        return

    await channel.send("Code loaded and running.")


async def revert_to_backup(channel: discord.abc.Messageable) -> None:
    if not BACKUP_CODE_PATH.exists():
        await channel.send("No backup to restore.")
        return
    shutil.copyfile(BACKUP_CODE_PATH, GENERATED_CODE_PATH)
    await channel.send("Restored the previous version. Reloading...")
    try:
        await load_and_execute_code()
    except Exception as error:  # noqa: BLE001
        await channel.send(f"The backup failed to load: {error}")
        return
    await channel.send("Previous version is running.")


async def post_for_review(
    channel: discord.abc.Messageable, code: str
) -> None:
    last = None
    for chunk in split_into_chunks(code, size=1880):
        last = await channel.send(f"```python\n{chunk}```")
        if last is None:
            return
    if last is None:
        return
    PENDING_CODE[last.id] = code
    await last.add_reaction("✅")


intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


@client.event
async def on_ready() -> None:
    print(f"Logged in as {client.user}")
    if not ALLOWED_CHANNELS:
        print("ALLOWED_CHANNEL_IDS is empty; the bot will ignore every message.")
        return
    if GENERATED_CODE_PATH.exists():
        try:
            await load_and_execute_code()
            print("Loaded the saved generated code.")
        except Exception as error:  # noqa: BLE001
            print(f"Could not load {GENERATED_CODE_PATH}: {error}")


# The generated code also wants `on_message`. Both handlers are called through
# this router so a reload does not disconnect the `$add` / `$undo` commands.
async def on_message(message: discord.Message) -> None:
    if GENERATED_ON_MESSAGE is not None:
        try:
            await GENERATED_ON_MESSAGE(message)
        except Exception:  # noqa: BLE001 - one bad generated handler must not stop the bot
            traceback.print_exc()
    await dispatch_message(message)


client.event(on_message)


async def dispatch_message(message: discord.Message) -> None:
    if message.author == client.user or message.webhook_id is not None:
        return
    if message.channel.id not in ALLOWED_CHANNELS:
        return
    if not message.content:
        return

    command = message.content.split(maxsplit=1)[0].lower()
    if command in ("$undo", "$restore"):
        await revert_to_backup(message.channel)
        return

    if command in CODE_COMMANDS:
        instruction = message.content[len(command):].strip()
        if not instruction:
            await message.channel.send(f"Usage: {command} <what to change>")
            return
        verb = {
            "$add": "Add this feature",
            "$change": "Update the program for this",
            "$remove": "Remove this feature",
        }[command]
        prompt = build_rewrite_prompt(instruction, verb)
    else:
        prompt = FRIEND_PROMPT + message.content

    try:
        reply = await asyncio.to_thread(ask_llm, prompt)
    except Exception as error:  # noqa: BLE001
        await message.channel.send(f"The model call failed: {error}")
        return

    if command in CODE_COMMANDS:
        code = extract_code(reply)
        if code is None:
            await message.channel.send(
                "The model did not return a Python code block. Raw reply:"
            )
            for chunk in split_into_chunks(reply):
                await message.channel.send(chunk)
            return
        await post_for_review(message.channel, code)
    else:
        for chunk in split_into_chunks(reply):
            await message.channel.send(chunk)


@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
    if str(payload.emoji) != "✅":
        return
    if payload.channel_id not in ALLOWED_CHANNELS:
        return
    if payload.member is not None and payload.member.bot:
        return

    code = PENDING_CODE.pop(payload.message_id, None)
    if code is None:
        return

    channel = client.get_channel(payload.channel_id)
    if channel is None:
        return
    try:
        await apply_code(channel, code)
    except Exception as error:  # noqa: BLE001
        await channel.send(f"Reload failed, the bot may be in a broken state: {error}")


token = os.getenv("DISCORD_TOKEN")
if __name__ == "__main__":
    if not token:
        sys.exit("DISCORD_TOKEN is not set (see .env.example)")
    client.run(token)
