#Chopper, an RPS bot for BNS games
#By Matthew Lynn, 6/20/2026
#v2.0.1

"""
rps.py

Discord Rock, Paper, Scissors "Chop" Bot

Version:
    v2.0.1

Command examples:
    chop
    chop help
    chop ?
    chop version
    chop @player rock 7
    chop @player rock
    chop reality rock 7
    chop bot paper

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
    - A Defender may Relent, which causes an automatic loss.
    - If a Challenger omits the test pool, it defaults to 0.
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
BOT_VERSION = "2.0.1"

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
    challenger_defaulted_test_pool: bool = False
    defender_defaulted_test_pool: bool = False
    defender_relented: bool = False


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

    The public reason reveals whether the throw, test pool, or Relent decided
    the outcome, but never reveals test pool numbers.
    """

    if state.defender_relented:
        return state.challenger, "Defender relented."

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
    return state.defender, "Complete tie. Advantage falls to the Defender."


def build_notes(state: ChopState) -> str:
    """
    Builds public notes for defaulted values.

    Test pool numbers are not shown, but players are told when a missing
    test pool was treated as 0.
    """

    notes = []

    if state.challenger_defaulted_test_pool:
        notes.append("Challenger test pool was not entered and defaulted to 0.")

    if state.defender_defaulted_test_pool:
        notes.append("Defender test pool was not entered and defaulted to 0.")

    if not notes:
        return ""

    return "\n\n**Notes:**\n" + "\n".join(f"• {note}" for note in notes)


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

    defender_throw = "Relented" if state.defender_relented else format_throw(state.defender_throw)

    return (
        "**CHOP RESULT**\n\n"
        f"**Challenger:** {get_name(state.challenger)}\n"
        f"**Throw:** {format_throw(state.challenger_throw)}\n\n"
        f"**Defender:** {get_name(state.defender)}\n"
        f"**Throw:** {defender_throw}\n\n"
        f"**Winner:** {get_name(winner)}\n\n"
        f"**Reason:** {reason}"
        f"{build_notes(state)}"
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


def parse_test_pool(value: Optional[str]) -> tuple[int, bool]:
    """
    Converts test pool input to a non-negative integer.

    Returns:
        test_pool, defaulted

    If the value is missing or blank, the test pool defaults to 0.
    """

    if value is None or value.strip() == "":
        return 0, True

    try:
        test_pool = int(value.strip())
    except ValueError as exc:
        raise ValueError("The test pool must be a whole number of 0 or higher.") from exc

    if test_pool < 0:
        raise ValueError("The test pool must be a whole number of 0 or higher.")

    return test_pool, False


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
            placeholder="Enter your test pool number, or leave blank for 0",
            required=False,
            min_length=0,
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

        try:
            defender_test_pool, defaulted = parse_test_pool(str(self.test_pool_input.value))
        except ValueError as exc:
            await interaction.response.send_message(
                str(exc),
                ephemeral=True,
            )
            return

        self.state.defender_throw = self.defender_throw
        self.state.defender_test_pool = defender_test_pool
        self.state.defender_defaulted_test_pool = defaulted
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

        await interaction.response.send_modal(TestPoolModal(self.state, self.throw))


class RelentButton(Button):
    """Button that lets the Defender concede the chop."""

    def __init__(self, state: ChopState):
        super().__init__(
            label="Relent",
            style=discord.ButtonStyle.danger,
        )

        self.state = state

    async def callback(self, interaction: discord.Interaction) -> None:
        """Resolves the chop as an automatic Challenger win."""

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

        self.state.defender_relented = True
        self.state.resolved = True

        winner, reason = resolve_chop(self.state)
        result_message = build_result_message(self.state, winner, reason)

        if self.state.message:
            await self.state.message.edit(content=result_message, view=None)

        await interaction.response.send_message(
            "You have relented.",
            ephemeral=True,
        )


class ChopView(View):
    """Button view shown on the public challenge message."""

    def __init__(self, state: ChopState):
        super().__init__(timeout=CHOP_TIMEOUT_SECONDS)

        self.state = state

        self.add_item(ThrowButton("rock", state))
        self.add_item(ThrowButton("paper", state))
        self.add_item(ThrowButton("scissors", state))
        self.add_item(RelentButton(state))

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
        f"**CHOP HELP — v{BOT_VERSION}**\n\n"
        "**Challenge another player:**\n"
        "`chop @player rock 7`\n"
        "`chop @player rock` — test pool defaults to 0\n\n"
        "**Challenge Reality:**\n"
        "`chop reality rock 7`\n"
        "`chop bot rock` — test pool defaults to 0\n\n"
        "**Other commands:**\n"
        "`chop help`\n"
        "`chop ?`\n"
        "`chop version`\n\n"
        "**Valid throws:**\n"
        "`rock`, `paper`, `scissors`\n"
        "Aliases: `r`, `p`, `s`\n\n"
        "**Rules:**\n"
        "• Rock defeats Scissors.\n"
        "• Scissors defeats Paper.\n"
        "• Paper defeats Rock.\n"
        "• If both players choose the same throw, the higher test pool wins.\n"
        "• Test pool numbers are never shown publicly.\n"
        "• If a test pool is not entered, it defaults to 0.\n"
        "• If both throw and test pool tie, advantage falls to the Defender.\n"
        "• The Defender may Relent, which is an automatic loss.\n"
        "• Against Reality, tied throws favor the Challenger.\n"
        "• Player Defenders have 4 minutes to respond."
    )

    await message.channel.send(help_text)


async def safely_delete_message(message: discord.Message) -> None:
    """
    Deletes a message when possible.

    This is important because the original command includes the Challenger's
    throw and possibly their test pool.
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
    challenger_defaulted_test_pool: bool,
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
        challenger_defaulted_test_pool=challenger_defaulted_test_pool,
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
    challenger_defaulted_test_pool: bool,
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
        challenger_defaulted_test_pool=challenger_defaulted_test_pool,
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
        chop
        chop @player rock
        chop @player rock 7
        chop reality rock
        chop reality rock 7
        chop bot rock
        chop bot rock 7
        chop help
        chop ?
        chop version
    """

    parts = message.content.split()

    # Bare "chop" should behave exactly like help.
    if len(parts) == 1:
        await send_chop_help(message)
        return

    subcommand = parts[1].lower()

    if subcommand in {"help", "?"}:
        await send_chop_help(message)
        return

    if subcommand == "version":
        await message.channel.send(f"RPS Bot Version: v{BOT_VERSION}")
        return

    # Valid challenge forms are:
    #     chop @player rock
    #     chop @player rock 7
    #     chop reality rock
    #     chop reality rock 7
    if len(parts) not in {3, 4}:
        await send_temporary_error(
            message.channel,
            "Use `chop help` for syntax.",
        )
        return

    _, target_text, throw_text, *test_pool_parts = parts

    challenger_throw = normalize_throw(throw_text)

    # If no rock/paper/scissors value is entered, tell the user and close out.
    if challenger_throw is None:
        await send_temporary_error(
            message.channel,
            "No valid throw was entered. Use `rock`, `paper`, or `scissors`.",
        )
        return

    test_pool_text = test_pool_parts[0] if test_pool_parts else None

    try:
        challenger_test_pool, challenger_defaulted = parse_test_pool(test_pool_text)
    except ValueError as exc:
        await send_temporary_error(message.channel, str(exc))
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
            challenger_defaulted_test_pool=challenger_defaulted,
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
        challenger_defaulted_test_pool=challenger_defaulted,
    )


# ---------------------------------------------------------------------------
# Discord Events
# ---------------------------------------------------------------------------

@client.event
async def on_ready() -> None:
    """Runs when the bot successfully logs in."""

    print(f"RPS Bot v{BOT_VERSION} logged in as {client.user}")


@client.event
async def on_message(message: discord.Message) -> None:
    """Watches for chop commands."""

    if message.author.bot:
        return

    if not message.content.lower().startswith(COMMAND_WORD):
        return

    # Only treat the message as a command when it is exactly "chop" or when
    # it starts with "chop ". This prevents "chopped" from triggering the bot.
    lowered = message.content.lower()
    if lowered != COMMAND_WORD and not lowered.startswith(f"{COMMAND_WORD} "):
        return

    await handle_chop_command(message)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing from the .env file.")

client.run(DISCORD_TOKEN)