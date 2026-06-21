#Chopper, an RPS bot for BNS games
#By Matthew Lynn, 6/20/2026
#Test change2

"""
Rock, Paper, Scissors "Chop" Bot

Command format:
    chop @player rock 7

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
    - The Defender has 4 minutes to respond.
    - The public challenge message is edited/replaced as the chop progresses.
"""

import os
from dataclasses import dataclass
from typing import Optional

import chopper
from chopper.ui import Button, Modal, TextInput, View
from dotenv import load_dotenv


# -----------------------------
# Configuration
# -----------------------------

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

COMMAND_WORD = "chop"
CHOP_TIMEOUT_SECONDS = 240  # 4 minutes

VALID_THROWS = {"rock", "paper", "scissors"}

# Each key defeats the listed value.
WIN_MAP = {
    "rock": "scissors",
    "paper": "rock",
    "scissors": "paper",
}


# -----------------------------
# Discord Client Setup
# -----------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)


# -----------------------------
# Data Model
# -----------------------------

@dataclass
class ChopState:
    """Stores all private information needed to resolve one chop."""

    challenger: discord.Member
    defender: discord.Member

    challenger_throw: str
    challenger_test_pool: int

    defender_throw: Optional[str] = None
    defender_test_pool: Optional[int] = None

    message: Optional[discord.Message] = None
    resolved: bool = False


# -----------------------------
# Helper Functions
# -----------------------------

def normalize_throw(value: str) -> Optional[str]:
    """Accepts full words and short aliases for throws."""

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


def format_throw(value: str) -> str:
    """Formats a throw for public display."""

    return value.capitalize()


def resolve_chop(state: ChopState) -> tuple[discord.Member, str]:
    """
    Resolves the chop.

    Returns:
        winner, reason
    """

    challenger_throw = state.challenger_throw
    defender_throw = state.defender_throw

    if defender_throw is None or state.defender_test_pool is None:
        raise ValueError("Cannot resolve chop before Defender response is complete.")

    # Normal RPS resolution.
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

    # Same throw and same test pool: Defender wins.
    return state.defender, "Complete tie. Advantage falls to the Defender."


def build_pending_message(state: ChopState) -> str:
    """Creates the public pending challenge message."""

    return (
        "**CHOP CHALLENGE**\n\n"
        f"**Challenger:** {state.challenger.mention}\n"
        f"**Defender:** {state.defender.mention}\n\n"
        "Awaiting Defender response.\n"
        "This challenge will expire in 4 minutes."
    )


def build_result_message(state: ChopState, winner: discord.Member, reason: str) -> str:
    """Creates the final public result message."""

    return (
        "**CHOP RESULT**\n\n"
        f"**Challenger:** {state.challenger.mention}\n"
        f"**Throw:** {format_throw(state.challenger_throw)}\n\n"
        f"**Defender:** {state.defender.mention}\n"
        f"**Throw:** {format_throw(state.defender_throw)}\n\n"
        f"**Winner:** {winner.mention}\n\n"
        f"**Reason:** {reason}"
    )


def build_expired_message(state: ChopState) -> str:
    """Creates the public timeout message."""

    return (
        "**CHOP CHALLENGE EXPIRED**\n\n"
        f"**Challenger:** {state.challenger.mention}\n"
        f"**Defender:** {state.defender.mention}\n\n"
        "**Result:** No contest.\n"
        "**Reason:** The Defender did not respond within 4 minutes."
    )


# -----------------------------
# Discord UI
# -----------------------------

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

        if interaction.user.id != self.state.defender.id:
            await interaction.response.send_message(
                "Only the Defender may respond to this challenge.",
                ephemeral=True,
            )
            return

        try:
            defender_test_pool = int(str(self.test_pool_input.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "The test pool must be a whole number.",
                ephemeral=True,
            )
            return

        if defender_test_pool < 0:
            await interaction.response.send_message(
                "The test pool cannot be negative.",
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

        if interaction.user.id != self.state.defender.id:
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


# -----------------------------
# Command Handling
# -----------------------------

async def handle_chop_command(message: discord.Message) -> None:
    """
    Handles:
        chop @player rock 7
    """

    parts = message.content.split()

    if len(parts) != 4:
        await message.channel.send(
            "Use this format: `chop @player rock 7`",
            delete_after=12,
        )
        return

    _, mention_text, throw_text, test_pool_text = parts

    if not message.mentions:
        await message.channel.send(
            "You need to challenge a player, like this: `chop @player rock 7`",
            delete_after=12,
        )
        return

    challenger = message.author
    defender = message.mentions[0]

    if defender.bot:
        await message.channel.send(
            "You cannot challenge a bot with this command.",
            delete_after=12,
        )
        return

    if defender.id == challenger.id:
        await message.channel.send(
            "You cannot challenge yourself. The mirror refuses to play.",
            delete_after=12,
        )
        return

    challenger_throw = normalize_throw(throw_text)

    if challenger_throw is None:
        await message.channel.send(
            "Valid throws are `rock`, `paper`, or `scissors`.",
            delete_after=12,
        )
        return

    try:
        challenger_test_pool = int(test_pool_text)
    except ValueError:
        await message.channel.send(
            "The test pool must be a whole number.",
            delete_after=12,
        )
        return

    if challenger_test_pool < 0:
        await message.channel.send(
            "The test pool cannot be negative.",
            delete_after=12,
        )
        return

    # Delete the command message so the Challenger's throw and test pool
    # are not left sitting in the channel.
    #
    # This requires the bot to have Manage Messages permission.
    try:
        await message.delete()
    except discord.Forbidden:
        await message.channel.send(
            "I need Manage Messages permission to hide chop commands properly.",
            delete_after=12,
        )
    except discord.HTTPException:
        pass

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


# -----------------------------
# Discord Events
# -----------------------------

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


# -----------------------------
# Startup
# -----------------------------

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing from the .env file.")

client.run(DISCORD_TOKEN)