#Chopper, an RPS bot for BNS games
#By Matthew Lynn, 6/20/2026
#v2.0.1

"""
rps.py

Discord Rock, Paper, Scissors "Chop" Bot

Command examples:
    chop @player rock 7
    chop reality rock 7
    chop bot paper 4
    chop help
    chop ?

Terms:
    Challenger - the player who starts the chop.
    Defender   - the player being challenged.

Rules:
    - Rock beats Scissors.
    - Scissors beats Paper.
    - Paper beats Rock.
    - If both players choose the same throw, compare test pools.
    - Test pool numbers are never shown publicly.
    - If both throw and test pool are tied, the Defender wins.
    - Against Reality, Reality's test pool is -1, so tied throws favor the Challenger.
    - Player Defenders have 4 minutes to respond.
    - The bot edits/replaces the same public message during the challenge.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from typing import Optional, Union

import discord
from discord.ui import Button, Modal, TextInput, View
from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

COMMAND_WORD = "chop"

# Defender response window.
CHOP_TIMEOUT_SECONDS = 240  # 4 minutes

# Reality is the bot-controlled opponent.
REALITY_NAME = "Reality"
REALITY_KEYWORDS = {"reality", "bot"}

# Reality should lose any tied throw, even if the Challenger enters 0.
REALITY_TEST_POOL = -1

VALID_THROWS = {"rock", "paper", "scissors"}

# Each key defeats the listed value.
WIN_MAP = {
    "rock": "scissors",
    "paper": "rock",
    "scissors": "paper",
}



# Permissions required for the bot to operate correctly.
REQUIRED_PERMISSIONS = {
    "View Channels": "view_channel",
    "Send Messages": "send_messages",
    "Read Message History": "read_message_history",
    "Manage Messages": "manage_messages",
}

# ---------------------------------------------------------------------------
# Discord Client Setup
# ---------------------------------------------------------------------------

intents = discord.Intents.default()

# Required because this bot uses a prefix command:
#     chop @player rock 7
#
# This must also be enabled in the Discord Developer Portal.
intents.message_content = True

# Helpful for working with Discord members.
intents.members = True

client = discord.Client(intents=intents)


# ---------------------------------------------------------------------------
# Data Model
# ---------------------------------------------------------------------------

Player = Union[discord.Member, str]


@dataclass
class ChopState:
    """
    Stores all information needed to resolve one chop.

    The test pool values are intentionally kept private and are never shown
    in the public result message.
    """

    challenger: discord.Member
    defender: Player

    challenger_throw: str
    challenger_test_pool: int

    defender_throw: Optional[str] = None
    defender_test_pool: Optional[int] = None

    message: Optional[discord.Message] = None
    resolved: bool = False
    is_reality_challenge: bool = False


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def normalize_throw(value: str) -> Optional[str]:
    """
    Converts player input into a valid throw.

    Accepts:
        rock, paper, scissors
        r, p, s
        scissor
    """

    value = value.lower().strip()

    aliases = {
        "r": "rock",
        "p": "paper",
        "s": "scissors",
        "scissor": "scissors",
    }

    value = aliases.get(value, value)

    if value in VALID_THROWS:
        return value

    return None


def format_throw(value: Optional[str]) -> str:
    """Formats a throw for public display."""

    if value is None:
        return "Unknown"

    return value.capitalize()


def get_name(player: Player) -> str:
    """
    Returns a clean display name.

    Discord members are shown as mentions.
    Reality is shown as plain text.
    """

    if isinstance(player, str):
        return player

    return player.mention


def get_player_id(player: Player) -> Optional[int]:
    """
    Returns a Discord user ID when the player is a Discord member.

    Reality has no Discord user ID.
    """

    if isinstance(player, str):
        return None

    return player.id


def resolve_chop(state: ChopState) -> tuple[Player, str]:
    """
    Resolves the chop.

    Returns:
        winner, reason

    The public reason reveals whether the throw or test pool decided the
    outcome, but never reveals test pool numbers.
    """

    challenger_throw = state.challenger_throw
    defender_throw = state.defender_throw

    if defender_throw is None or state.defender_test_pool is None:
        raise ValueError("Cannot resolve chop before Defender response is complete.")

    # Normal Rock, Paper, Scissors resolution.
    if challenger_throw != defender_throw:
        if WIN_MAP[challenger_throw] == defender_throw:
            return (
                state.challenger,
                f"{format_throw(challenger_throw)} defeats {format_throw(defender_throw)}.",
            )

        return (
            state.defender,
            f"{format_throw(defender_throw)} defeats {format_throw(challenger_throw)}.",
        )

    # Same throw: test pool decides.
    if state.challenger_test_pool > state.defender_test_pool:
        return state.challenger, "Victory secured by test pool."

    if state.defender_test_pool > state.challenger_test_pool:
        return state.defender, "Victory secured by test pool."

    # Same throw and same test pool: normal Defender advantage.
    #
    # Reality uses a test pool of -1, so this branch should not occur during
    # Reality challenges unless the Challenger somehow enters -1, which the
    # input validation rejects.
    return state.defender, "Complete tie. Advantage falls to the Defender."


def build_pending_message(state: ChopState) -> str:
    """Creates the public pending challenge message."""

    return (
        "**CHOP CHALLENGE**\n\n"
        f"**Challenger:** {get_name(state.challenger)}\n"
        f"**Defender:** {get_name(state.defender)}\n\n"
        "Awaiting Defender response.\n"
        "This challenge will expire in 4 minutes."
    )


def build_result_message(state: ChopState, winner: Player, reason: str) -> str:
    """Creates the final public result message."""

    return (
        "**CHOP RESULT**\n\n"
        f"**Challenger:** {get_name(state.challenger)}\n"
        f"**Throw:** {format_throw(state.challenger_throw)}\n\n"
        f"**Defender:** {get_name(state.defender)}\n"
        f"**Throw:** {format_throw(state.defender_throw)}\n\n"
        f"**Winner:** {get_name(winner)}\n\n"
        f"**Reason:** {reason}"
    )


def build_expired_message(state: ChopState) -> str:
    """Creates the public timeout message."""

    return (
        "**CHOP CHALLENGE EXPIRED**\n\n"
        f"**Challenger:** {get_name(state.challenger)}\n"
        f"**Defender:** {get_name(state.defender)}\n\n"
        "**Result:** No contest.\n"
        "**Reason:** The Defender did not respond within 4 minutes."
    )


def parse_test_pool(value: str) -> Optional[int]:
    """
    Converts test pool input to a non-negative integer.

    Returns None when the input is invalid.
    """

    try:
        test_pool = int(value.strip())
    except ValueError:
        return None

    if test_pool < 0:
        return None

    return test_pool



# ---------------------------------------------------------------------------
# Permission Checks
# ---------------------------------------------------------------------------

async def send_permission_report(message: discord.Message) -> None:
    if message.guild is None:
        await message.channel.send("Permission checks can only be run in a server.")
        return

    me = message.guild.me
    guild_perms = me.guild_permissions
    channel_perms = message.channel.permissions_for(me)

    lines = [
        f"**Chopper Permissions — v{BOT_VERSION}**",
        "",
        "**Verbose Report**",
        ""
    ]

    problems = False

    for display_name, attr in REQUIRED_PERMISSIONS.items():
        server_ok = getattr(guild_perms, attr)
        channel_ok = getattr(channel_perms, attr)

        if not server_ok or not channel_ok:
            problems = True

        lines.append(f"**{display_name}**")
        lines.append(f"  Server: {'✓' if server_ok else '✗'}")
        lines.append(f"  Channel: {'✓' if channel_ok else '✗'}")

    lines.append("")
    lines.append("Result: Permission check passed." if not problems else "Result: Missing permissions detected.")

    await message.channel.send("\n".join(lines))

async def check_permissions_on_startup() -> None:
    print("=" * 60)
    print("Chopper Permission Check")
    for guild in client.guilds:
        me = guild.me
        missing = []
        for display_name, attr in REQUIRED_PERMISSIONS.items():
            if not getattr(me.guild_permissions, attr):
                missing.append(display_name)
        if missing:
            print(f"{guild.name}: Missing -> {', '.join(missing)}")
        else:
            print(f"{guild.name}: Permission check passed.")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Discord UI
# ---------------------------------------------------------------------------

class TestPoolModal(Modal):
    """Modal used to collect the Defender's private test pool."""

    def __init__(self, state: ChopState, defender_throw: str):
        super().__init__(title="Submit Test Pool")

        self.state = state
        self.defender_throw = defender_throw

        self.test_pool_input = TextInput(
            label="Test Pool",
            placeholder="Enter your test pool number",
            required=True,
            min_length=1,
            max_length=6,
        )

        self.add_item(self.test_pool_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Records the Defender's test pool and resolves the chop."""

        defender_id = get_player_id(self.state.defender)

        if defender_id is None or interaction.user.id != defender_id:
            await interaction.response.send_message(
                "Only the Defender may respond to this challenge.",
                ephemeral=True,
            )
            return

        defender_test_pool = parse_test_pool(str(self.test_pool_input.value))

        if defender_test_pool is None:
            await interaction.response.send_message(
                "The test pool must be a whole number of 0 or higher.",
                ephemeral=True,
            )
            return

        self.state.defender_throw = self.defender_throw
        self.state.defender_test_pool = defender_test_pool
        self.state.resolved = True

        winner, reason = resolve_chop(self.state)
        result_message = build_result_message(self.state, winner, reason)

        if self.state.message:
            await self.state.message.edit(content=result_message, view=None)

        await interaction.response.send_message(
            "Your response has been recorded.",
            ephemeral=True,
        )


class ThrowButton(Button):
    """Button representing one Defender throw option."""

    def __init__(self, throw: str, state: ChopState):
        super().__init__(
            label=format_throw(throw),
            style=discord.ButtonStyle.primary,
        )

        self.throw = throw
        self.state = state

    async def callback(self, interaction: discord.Interaction) -> None:
        """Only allows the Defender to choose a throw."""

        defender_id = get_player_id(self.state.defender)

        if defender_id is None or interaction.user.id != defender_id:
            await interaction.response.send_message(
                "Only the Defender may answer this challenge.",
                ephemeral=True,
            )
            return

        if self.state.resolved:
            await interaction.response.send_message(
                "This challenge has already been resolved.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            TestPoolModal(self.state, self.throw)
        )


class ChopView(View):
    """Button view shown on the public challenge message."""

    def __init__(self, state: ChopState):
        super().__init__(timeout=CHOP_TIMEOUT_SECONDS)

        self.state = state

        self.add_item(ThrowButton("rock", state))
        self.add_item(ThrowButton("paper", state))
        self.add_item(ThrowButton("scissors", state))

    async def on_timeout(self) -> None:
        """Expires the challenge after 4 minutes."""

        if self.state.resolved:
            return

        self.state.resolved = True

        if self.state.message:
            await self.state.message.edit(
                content=build_expired_message(self.state),
                view=None,
            )


# ---------------------------------------------------------------------------
# Command Helpers
# ---------------------------------------------------------------------------

async def send_temporary_error(
    channel: discord.abc.Messageable,
    text: str,
    delete_after: int = 12,
) -> None:
    """Sends a short-lived error message."""

    await channel.send(text, delete_after=delete_after)


async def send_chop_help(message: discord.Message) -> None:
    """Displays syntax and rules for the Chop bot."""

    help_text = (
        "**CHOP HELP**\n\n"
        "**Challenge another player:**\n"
        "`chop @player rock 7`\n\n"
        "**Challenge Reality:**\n"
        "`chop reality rock 7`\n"
        "`chop bot rock 7`\n\n"
        "**Valid throws:**\n"
        "`rock`, `paper`, `scissors`\n"
        "Aliases: `r`, `p`, `s`\n\n"
        "**Rules:**\n"
        "• Rock defeats Scissors.\n"
        "• Scissors defeats Paper.\n"
        "• Paper defeats Rock.\n"
        "• If both players choose the same throw, the higher test pool wins.\n"
        "• Test pool numbers are never shown publicly.\n"
        "• If both throw and test pool tie, advantage falls to the Defender.\n"
        "• Against Reality, tied throws favor the Challenger.\n"
        "• Player Defenders have 4 minutes to respond."
    )

    await message.channel.send(help_text)


async def safely_delete_message(message: discord.Message) -> None:
    """
    Deletes a message when possible.

    This is important because the original command includes the Challenger's
    throw and test pool.
    """

    try:
        await message.delete()
    except discord.Forbidden:
        await message.channel.send(
            "I need Manage Messages permission to hide chop commands properly.",
            delete_after=12,
        )
    except discord.HTTPException:
        # Ignore transient Discord API errors.
        pass


async def handle_reality_challenge(
    message: discord.Message,
    challenger_throw: str,
    challenger_test_pool: int,
) -> None:
    """
    Resolves a chop directly against Reality.

    Reality does not use buttons or modals. The bot immediately chooses a
    random throw and uses a test pool of -1.
    """

    state = ChopState(
        challenger=message.author,
        defender=REALITY_NAME,
        challenger_throw=challenger_throw,
        challenger_test_pool=challenger_test_pool,
        defender_throw=random.choice(sorted(VALID_THROWS)),
        defender_test_pool=REALITY_TEST_POOL,
        resolved=True,
        is_reality_challenge=True,
    )

    winner, reason = resolve_chop(state)
    result_message = build_result_message(state, winner, reason)

    await safely_delete_message(message)
    await message.channel.send(result_message)


async def handle_player_challenge(
    message: discord.Message,
    defender: discord.Member,
    challenger_throw: str,
    challenger_test_pool: int,
) -> None:
    """Creates a player-vs-player challenge."""

    challenger = message.author

    if defender.bot:
        await send_temporary_error(
            message.channel,
            "You cannot challenge a bot with this command. Use `chop reality rock 7` instead.",
        )
        return

    if defender.id == challenger.id:
        await send_temporary_error(
            message.channel,
            "You cannot challenge yourself. The mirror refuses to play.",
        )
        return

    await safely_delete_message(message)

    state = ChopState(
        challenger=challenger,
        defender=defender,
        challenger_throw=challenger_throw,
        challenger_test_pool=challenger_test_pool,
    )

    view = ChopView(state)

    challenge_message = await message.channel.send(
        build_pending_message(state),
        view=view,
    )

    state.message = challenge_message


async def handle_chop_command(message: discord.Message) -> None:
    """
    Handles all chop commands.

    Supported:
        chop @player rock 7
        chop reality rock 7
        chop bot rock 7
        chop help
        chop ?
    """

    parts = message.content.split()

    if len(parts) >= 2 and parts[1].lower() in {"help", "?"}:
        await send_chop_help(message)
        return

    if len(parts) != 4:
        await send_temporary_error(
            message.channel,
            "Use this format: `chop @player rock 7` or `chop reality rock 7`.",
        )
        return

    _, target_text, throw_text, test_pool_text = parts

    challenger_throw = normalize_throw(throw_text)

    if challenger_throw is None:
        await send_temporary_error(
            message.channel,
            "Valid throws are `rock`, `paper`, or `scissors`.",
        )
        return

    challenger_test_pool = parse_test_pool(test_pool_text)

    if challenger_test_pool is None:
        await send_temporary_error(
            message.channel,
            "The test pool must be a whole number of 0 or higher.",
        )
        return

    # Reality challenge.
    #
    # Check this before mention handling so the keyword is reserved and
    # behaves predictably.
    if target_text.lower() in REALITY_KEYWORDS:
        await handle_reality_challenge(
            message=message,
            challenger_throw=challenger_throw,
            challenger_test_pool=challenger_test_pool,
        )
        return

    if not message.mentions:
        await send_temporary_error(
            message.channel,
            "You need to challenge a player, like this: `chop @player rock 7`.",
        )
        return

    defender = message.mentions[0]

    await handle_player_challenge(
        message=message,
        defender=defender,
        challenger_throw=challenger_throw,
        challenger_test_pool=challenger_test_pool,
    )


# ---------------------------------------------------------------------------
# Discord Events
# ---------------------------------------------------------------------------

@client.event
async def on_ready() -> None:
    """Runs when the bot successfully logs in."""

    print(f"Logged in as {client.user}")


@client.event
async def on_message(message: discord.Message) -> None:
    """Watches for chop commands."""

    if message.author.bot:
        return

    if not message.content.lower().startswith(f"{COMMAND_WORD} "):
        return

    await handle_chop_command(message)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing from the .env file.")

client.run(DISCORD_TOKEN)