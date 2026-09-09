#Chopper, an RPS bot for BNS games
#By Matthew Lynn, 6/20/2026
#v2.0.12

"""
rps.py

Discord Rock, Paper, Scissors "Chop" Bot

Version:
    v2.0.12

Command examples:
    chop
    chop help
    chop ?
    chop version
    chop permissions
    chop clean
    chop clean-up
    chop purge
    chop @player rock 7
    chop @player rock
    chop @player rock ?
    chop @player ? ?
    chop reality rock 7
    chop reality rock ?
    chop reality ? ?
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
from typing import Optional

import discord
from discord.ui import Button, Modal, TextInput, View
from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

COMMAND_WORD = "chop"
BOT_VERSION = "2.0.12"

# Defender response window.
CHOP_TIMEOUT_SECONDS = 240  # 4 minutes
BAD_COMMAND_HELP_TIMEOUT_SECONDS = 15
TEST_POOL_PROMPT_TIMEOUT_SECONDS = 60
TEMPORARY_ERROR_SECONDS = 12
CLEANUP_SUMMARY_SECONDS = 15

HELP_SUBCOMMANDS = {"help", "?"}
CLEANUP_SUBCOMMANDS = {"clean", "clean-up", "purge"}

# Reality is the bot-controlled opponent.
REALITY_NAME = "Reality"
REALITY_KEYWORDS = {"reality", "bot"}

# Reality should lose any tied throw, even if the Challenger enters 0.
REALITY_TEST_POOL = -1

VALID_THROWS = {"rock", "paper", "scissors"}
THROW_ALIASES = {
    "r": "rock",
    "p": "paper",
    "s": "scissors",
    "scissor": "scissors",
}

# Each key defeats the listed value.
WIN_MAP = {
    "rock": "scissors",
    "paper": "rock",
    "scissors": "paper",
}

# Permissions required for Chopper to operate correctly.
#
# Manage Messages is especially important because the original chop command
# contains the Challenger's throw and test pool. Without it, the command may
# remain visible in the channel and compromise the chop.
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

_startup_permission_check_complete = False


# ---------------------------------------------------------------------------
# Data Model
# ---------------------------------------------------------------------------

Player = discord.Member | str


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

    value = THROW_ALIASES.get(value, value)

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
# Discord Utility Helpers
# ---------------------------------------------------------------------------

async def delete_message_quietly(
    message: Optional[discord.Message],
) -> None:
    """Deletes a message without surfacing cleanup-only Discord errors."""

    if message is None:
        return

    try:
        await message.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


async def reject_unauthorized_interaction(
    interaction: discord.Interaction,
    expected_user_id: int,
    error_text: str,
) -> bool:
    """
    Rejects an interaction from anyone except the expected user.

    Returns True when the interaction was rejected so callers can simply
    return from the callback.
    """

    if interaction.user.id == expected_user_id:
        return False

    await interaction.response.send_message(
        error_text,
        ephemeral=True,
    )
    return True


# ---------------------------------------------------------------------------
# Permission Checks
# ---------------------------------------------------------------------------

def get_permission_status(
    guild: discord.Guild,
    channel: discord.abc.GuildChannel,
) -> list[tuple[str, bool, bool]]:
    """
    Returns verbose permission status for the bot.

    Each row contains:
        permission display name, server-level result, channel-level result

    Server-level permissions tell us whether the bot has the permission from
    its roles. Channel-level permissions tell us whether channel overwrites are
    allowing or blocking that permission in the current channel.
    """

    if guild.me is None:
        return [("Unable to identify bot member in this server.", False, False)]

    server_permissions = guild.me.guild_permissions
    channel_permissions = channel.permissions_for(guild.me)

    status = []

    for display_name, permission_attribute in REQUIRED_PERMISSIONS.items():
        server_ok = getattr(server_permissions, permission_attribute)
        channel_ok = getattr(channel_permissions, permission_attribute)
        status.append((display_name, server_ok, channel_ok))

    return status


def get_missing_guild_permissions(guild: discord.Guild) -> list[str]:
    """
    Returns required permissions missing at the server level.

    This is used for startup diagnostics. It does not catch channel overwrites;
    `chop permissions` handles that by checking the current channel too.
    """

    if guild.me is None:
        return ["Unable to identify bot member in this server."]

    missing_permissions = []
    permissions = guild.me.guild_permissions

    for display_name, permission_attribute in REQUIRED_PERMISSIONS.items():
        if not getattr(permissions, permission_attribute):
            missing_permissions.append(display_name)

    return missing_permissions


async def check_permissions_on_startup() -> None:
    """
    Prints a server-level permission report when the bot starts.

    This gives the host immediate feedback in the console if the bot is missing
    something important before players begin testing.
    """

    print("=" * 60)
    print("Chopper Permission Check")

    if not client.guilds:
        print("The bot is not currently connected to any servers.")
        print("=" * 60)
        return

    any_missing = False

    for guild in client.guilds:
        missing_permissions = get_missing_guild_permissions(guild)

        if missing_permissions:
            any_missing = True
            print(f"WARNING: {guild.name} is missing server-level permissions:")
            for permission in missing_permissions:
                print(f"  - {permission}")
        else:
            print(f"{guild.name}: Server-level permission check passed.")

    if not any_missing:
        print("All server-level permission checks passed.")

    print("=" * 60)


async def send_permission_report(message: discord.Message) -> None:
    """
    Sends a verbose permission report for the current channel.

    Command:
        chop permissions
    """

    if message.guild is None:
        await message.channel.send(
            "Permission checks can only be run inside a server channel."
        )
        return

    status_rows = get_permission_status(message.guild, message.channel)

    lines = [
        f"**Chopper Permissions — v{BOT_VERSION}**",
        "",
        "**Verbose Permission Report**",
        f"**Server:** {message.guild.name}",
        f"**Channel:** {message.channel.mention}",
        "",
    ]

    problems_found = False

    for display_name, server_ok, channel_ok in status_rows:
        if not server_ok or not channel_ok:
            problems_found = True

        lines.append(f"**{display_name}**")
        lines.append(f"Server: {'✓' if server_ok else '✗'}")
        lines.append(f"Channel: {'✓' if channel_ok else '✗'}")
        lines.append("")

    if problems_found:
        lines.append("**Result:** Missing permissions detected.")
        lines.append("")
        lines.append(
            "If Server is ✓ but Channel is ✗, a channel override is blocking the bot."
        )
        lines.append(
            "If Server is ✗, update the bot role or invite permissions."
        )
        lines.append(
            "Manage Messages is required to hide the Challenger's original chop."
        )
    else:
        lines.append("**Result:** Permission check passed.")

    await message.channel.send("\n".join(lines))


# ---------------------------------------------------------------------------
# Discord UI
# ---------------------------------------------------------------------------


class ChallengerThrowButton(Button):
    """Button used when the Challenger enters ? instead of a throw."""

    def __init__(
        self,
        throw: str,
        original_message: discord.Message,
        target_text: str,
    ):
        super().__init__(
            label=format_throw(throw),
            style=discord.ButtonStyle.primary,
        )

        self.throw = throw
        self.original_message = original_message
        self.target_text = target_text

    async def callback(self, interaction: discord.Interaction) -> None:
        """
        Records the Challenger's throw and immediately opens test-pool entry.

        Because the button click is a Discord interaction, Chopper can open the
        private modal directly here without requiring a second button.
        """

        if await reject_unauthorized_interaction(
            interaction,
            self.original_message.author.id,
            "Only the Challenger may choose this throw.",
        ):
            return

        await interaction.response.send_modal(
            ChallengerTestPoolModal(
                original_message=self.original_message,
                target_text=self.target_text,
                challenger_throw=self.throw,
            )
        )

        view = self.view
        if isinstance(view, ChallengerThrowView):
            await delete_message_quietly(view.prompt_message)
            view.stop()


class ChallengerThrowView(View):
    """
    Temporary Rock/Paper/Scissors selector for the Challenger.

    Used by:
        chop @player ? ?
        chop reality ? ?
    """

    def __init__(
        self,
        original_message: discord.Message,
        target_text: str,
    ):
        super().__init__(timeout=TEST_POOL_PROMPT_TIMEOUT_SECONDS)

        self.original_message = original_message
        self.target_text = target_text
        self.prompt_message: Optional[discord.Message] = None

        self.add_item(
            ChallengerThrowButton(
                "rock",
                original_message,
                target_text,
            )
        )
        self.add_item(
            ChallengerThrowButton(
                "paper",
                original_message,
                target_text,
            )
        )
        self.add_item(
            ChallengerThrowButton(
                "scissors",
                original_message,
                target_text,
            )
        )

    async def on_timeout(self) -> None:
        """Deletes the temporary throw selector if it is ignored."""

        await delete_message_quietly(self.prompt_message)


async def request_challenger_throw(
    message: discord.Message,
    target_text: str,
) -> None:
    """
    Requests a private Challenger throw when ? replaces rock/paper/scissors.

    The original command is deleted before Chopper posts the selector. If
    deletion fails, the challenge is cancelled to avoid exposing game input.
    """

    if not await delete_challenger_command(message):
        return

    view = ChallengerThrowView(
        original_message=message,
        target_text=target_text,
    )

    prompt = await message.channel.send(
        f"{message.author.mention}, choose your throw.",
        view=view,
    )

    view.prompt_message = prompt



class ChallengerTestPoolModal(Modal):
    """
    Private modal used when the Challenger enters ? for the test pool.

    Discord only permits modals to be opened from an interaction, not directly
    from a prefix-command message. The bot therefore posts a temporary button
    that the Challenger clicks to open this modal.
    """

    def __init__(
        self,
        original_message: discord.Message,
        target_text: str,
        challenger_throw: str,
    ):
        super().__init__(title="Enter Challenger Test Pool")

        self.original_message = original_message
        self.target_text = target_text
        self.challenger_throw = challenger_throw

        self.test_pool_input = TextInput(
            label="Test Pool",
            placeholder="Enter your test pool number",
            required=True,
            min_length=1,
            max_length=6,
        )

        self.add_item(self.test_pool_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Validates the test pool and continues the original challenge."""

        try:
            challenger_test_pool, _ = parse_test_pool(
                str(self.test_pool_input.value)
            )
        except ValueError as exc:
            await interaction.response.send_message(
                str(exc),
                ephemeral=True,
            )
            return

        # Acknowledge the modal before continuing with message operations.
        # The original chop was already deleted before this modal was offered.
        await interaction.response.defer(ephemeral=True)

        if self.target_text.lower() in REALITY_KEYWORDS:
            await handle_reality_challenge(
                message=self.original_message,
                challenger_throw=self.challenger_throw,
                challenger_test_pool=challenger_test_pool,
                challenger_defaulted_test_pool=False,
                command_already_deleted=True,
            )
            return

        if not self.original_message.mentions:
            await interaction.followup.send(
                "I could not identify the Defender for that challenge.",
                ephemeral=True,
            )
            return

        defender = self.original_message.mentions[0]

        await handle_player_challenge(
            message=self.original_message,
            defender=defender,
            challenger_throw=self.challenger_throw,
            challenger_test_pool=challenger_test_pool,
            challenger_defaulted_test_pool=False,
            command_already_deleted=True,
        )


class ChallengerTestPoolView(View):
    """
    Temporary interaction used to request a Challenger test pool.

    Only the Challenger may open the modal. The request expires after 60
    seconds to avoid leaving stale UI in the channel.
    """

    def __init__(
        self,
        original_message: discord.Message,
        target_text: str,
        challenger_throw: str,
    ):
        super().__init__(timeout=TEST_POOL_PROMPT_TIMEOUT_SECONDS)

        self.original_message = original_message
        self.target_text = target_text
        self.challenger_throw = challenger_throw
        self.prompt_message: Optional[discord.Message] = None

    @discord.ui.button(
        label="Enter Test Pool",
        style=discord.ButtonStyle.primary,
    )
    async def enter_test_pool(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Opens the private Challenger test-pool modal."""

        if await reject_unauthorized_interaction(
            interaction,
            self.original_message.author.id,
            "Only the Challenger may enter this test pool.",
        ):
            return

        await interaction.response.send_modal(
            ChallengerTestPoolModal(
                original_message=self.original_message,
                target_text=self.target_text,
                challenger_throw=self.challenger_throw,
            )
        )

        await delete_message_quietly(self.prompt_message)

        self.stop()

    async def on_timeout(self) -> None:
        """Deletes the temporary test-pool request if it is ignored."""

        await delete_message_quietly(self.prompt_message)


async def request_challenger_test_pool(
    message: discord.Message,
    target_text: str,
    challenger_throw: str,
) -> None:
    """
    Requests the Challenger's test pool when ? is supplied.

    The original command contains the Challenger's throw, so it must be
    removed before Chopper posts any follow-up UI. If Chopper cannot delete
    the command, the challenge is aborted rather than exposing the throw.

    Because Discord cannot launch a modal directly from a prefix-command
    message, the bot then posts a temporary button that opens the private
    modal.
    """

    if not await delete_challenger_command(message):
        return

    view = ChallengerTestPoolView(
        original_message=message,
        target_text=target_text,
        challenger_throw=challenger_throw,
    )

    prompt = await message.channel.send(
        f"{message.author.mention}, click below to enter your test pool.",
        view=view,
    )

    view.prompt_message = prompt


class BadCommandHelpView(View):
    """
    Short-lived help offer shown after an apparent malformed Chopper command.

    Only the user who triggered the apparent command may use the button.
    The prompt removes itself after 15 seconds if ignored.
    """

    def __init__(self, requester: discord.Member):
        super().__init__(timeout=BAD_COMMAND_HELP_TIMEOUT_SECONDS)
        self.requester = requester
        self.prompt_message: Optional[discord.Message] = None

    @discord.ui.button(
        label="Show Help",
        style=discord.ButtonStyle.secondary,
    )
    async def show_help(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        """Shows the normal Chopper help output to the requesting user."""

        if await reject_unauthorized_interaction(
            interaction,
            self.requester.id,
            "Only the user who triggered this prompt may request the help output.",
        ):
            return

        # Acknowledge the button first, then remove the temporary prompt.
        await interaction.response.defer()

        await delete_message_quietly(self.prompt_message)

        # Reuse the standard help renderer so there is only one source of truth.
        await send_chop_help_from_channel(interaction.channel)

        self.stop()

    async def on_timeout(self) -> None:
        """Deletes the help offer after 15 seconds."""

        await delete_message_quietly(self.prompt_message)


async def offer_help_for_bad_command(message: discord.Message) -> None:
    """
    Offers help after an apparent malformed Chopper command.

    The prompt is intentionally temporary because messages beginning with
    "chop " can be false positives in normal conversation.
    """

    view = BadCommandHelpView(message.author)

    prompt = await message.channel.send(
        f"{message.author.mention}, that looks like it may have been a Chopper "
        "command I could not understand. Would you like to see the help output?\n"
        "*This prompt will automatically expire in 15 seconds.*",
        view=view,
    )

    view.prompt_message = prompt


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

        if defender_id is None:
            await interaction.response.send_message(
                "This challenge no longer has a valid Defender.",
                ephemeral=True,
            )
            return

        if await reject_unauthorized_interaction(
            interaction,
            defender_id,
            "Only the Defender may respond to this challenge.",
        ):
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

        if defender_id is None:
            await interaction.response.send_message(
                "This challenge no longer has a valid Defender.",
                ephemeral=True,
            )
            return

        if await reject_unauthorized_interaction(
            interaction,
            defender_id,
            "Only the Defender may answer this challenge.",
        ):
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

        if defender_id is None:
            await interaction.response.send_message(
                "This challenge no longer has a valid Defender.",
                ephemeral=True,
            )
            return

        if await reject_unauthorized_interaction(
            interaction,
            defender_id,
            "Only the Defender may answer this challenge.",
        ):
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
    delete_after: int = TEMPORARY_ERROR_SECONDS,
) -> None:
    """Sends a short-lived error message."""

    await channel.send(text, delete_after=delete_after)


async def send_chop_help_from_channel(
    channel: discord.abc.Messageable,
) -> None:
    """Displays syntax and rules for the Chop bot in the supplied channel."""

    help_text = (
        f"**CHOP HELP — v{BOT_VERSION}**\n\n"
        "**Challenge another player:**\n"
        "`chop @player rock 7`\n"
        "`chop @player rock` — test pool defaults to 0\n"
        "`chop @player rock ?` — prompts for private test-pool entry\n"
        "`chop @player ? ?` — prompts for throw, then private test-pool entry\n\n"
        "**Challenge Reality:**\n"
        "`chop reality rock 7`\n"
        "`chop reality rock ?` — prompts for private test-pool entry\n"
        "`chop reality ? ?` — prompts for throw, then private test-pool entry\n"
        "`chop bot rock` — test pool defaults to 0\n\n"
        "**Other commands:**\n"
        "`chop help`\n"
        "`chop ?`\n"
        "`chop version`\n"
        "`chop permissions`\n"
        "`chop clean` — remove Chopper-related posts from this channel\n"
        "`chop clean-up` — alias for `chop clean`\n"
        "`chop purge` — alias for `chop clean`\n\n"
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
        "• Enter `?` as the test pool to be prompted for private test-pool entry.\n"
        "• Enter `? ?` for throw and test pool to choose the throw with buttons, then enter the test pool privately.\n"
        "• If both throw and test pool tie, advantage falls to the Defender.\n"
        "• The Defender may Relent, which is an automatic loss.\n"
        "• Against Reality, tied throws favor the Challenger.\n"
        "• Player Defenders have 4 minutes to respond.\n"
        "• If a message appears to be a malformed Chopper command, Chopper will offer help.\n"
        "• That help offer automatically expires after 15 seconds if ignored."
    )

    await channel.send(help_text)


async def send_chop_help(message: discord.Message) -> None:
    """Displays syntax and rules for the Chop bot."""

    await send_chop_help_from_channel(message.channel)


async def delete_challenger_command(message: discord.Message) -> bool:
    """
    Deletes the Challenger's original command before any chop continues.

    Returns True only when the command was successfully removed. If deletion
    fails, Chopper cancels the challenge so the Challenger's throw is not
    exposed.
    """

    try:
        await message.delete()
        return True
    except discord.Forbidden:
        await message.channel.send(
            "**Chop Cancelled**\n"
            "The chop was unable to proceed due to a technical issue.",
            delete_after=TEMPORARY_ERROR_SECONDS,
        )
        return False
    except discord.HTTPException:
        await message.channel.send(
            "**Chop Cancelled**\n"
            "The chop was unable to proceed due to a technical issue.",
            delete_after=TEMPORARY_ERROR_SECONDS,
        )
        return False


async def handle_reality_challenge(
    message: discord.Message,
    challenger_throw: str,
    challenger_test_pool: int,
    challenger_defaulted_test_pool: bool,
    command_already_deleted: bool = False,
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

    if not command_already_deleted:
        if not await delete_challenger_command(message):
            return

    await message.channel.send(result_message)


async def handle_player_challenge(
    message: discord.Message,
    defender: discord.Member,
    challenger_throw: str,
    challenger_test_pool: int,
    challenger_defaulted_test_pool: bool,
    command_already_deleted: bool = False,
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

    if not command_already_deleted:
        if not await delete_challenger_command(message):
            return

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
        chop @player rock ?
        chop @player ? ?
    chop @player ? ?
        chop reality rock
        chop reality rock 7
        chop reality rock ?
        chop reality ? ?
    chop reality ? ?
        chop bot rock
        chop bot rock 7
        chop help
        chop ?
        chop version
        chop permissions
        chop clean
        chop clean-up
        chop purge
    """

    parts = message.content.split()

    # Bare "chop" should behave exactly like help.
    if len(parts) == 1:
        await send_chop_help(message)
        return

    subcommand = parts[1].lower()

    if subcommand in HELP_SUBCOMMANDS:
        await send_chop_help(message)
        return

    if subcommand == "version":
        await message.channel.send(f"Chopper Version: v{BOT_VERSION}")
        return

    if subcommand == "permissions":
        await send_permission_report(message)
        return

    if subcommand in CLEANUP_SUBCOMMANDS:
        await clean_chopper_posts(message)
        return

    # Valid challenge forms are:
    #     chop @player rock
    #     chop @player rock 7
    #     chop reality rock
    #     chop reality rock 7
    if len(parts) not in {3, 4}:
        await offer_help_for_bad_command(message)
        return

    _, target_text, throw_text, *test_pool_parts = parts

    is_reality_target = target_text.casefold() in REALITY_KEYWORDS

    # Validate the target before requesting any private Challenger input.
    if not is_reality_target and not message.mentions:
        await offer_help_for_bad_command(message)
        return

    test_pool_text = test_pool_parts[0] if test_pool_parts else None

    # New private-input form:
    #     chop @player ? ?
    #     chop reality ? ?
    #
    # The first ? presents Rock/Paper/Scissors buttons. Selecting a throw is
    # an interaction, so Chopper can immediately open the private test-pool
    # modal from that click.
    if throw_text == "?":
        if test_pool_text != "?":
            await offer_help_for_bad_command(message)
            return

        await request_challenger_throw(
            message=message,
            target_text=target_text,
        )
        return

    challenger_throw = normalize_throw(throw_text)

    if challenger_throw is None:
        await offer_help_for_bad_command(message)
        return

    # A question mark requests private entry of the Challenger's test pool.
    # The challenge continues only after the Challenger submits the modal.
    if test_pool_text == "?":
        await request_challenger_test_pool(
            message=message,
            target_text=target_text,
            challenger_throw=challenger_throw,
        )
        return

    try:
        challenger_test_pool, challenger_defaulted = parse_test_pool(test_pool_text)
    except ValueError:
        await offer_help_for_bad_command(message)
        return

    # Reality challenge.
    #
    # Check this before mention handling so the keyword is reserved and
    # behaves predictably.
    if is_reality_target:
        await handle_reality_challenge(
            message=message,
            challenger_throw=challenger_throw,
            challenger_test_pool=challenger_test_pool,
            challenger_defaulted_test_pool=challenger_defaulted,
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


async def clean_chopper_posts(message: discord.Message) -> None:
    """
    Removes Chopper-related traffic from the current channel.

    Command aliases:
        chop clean
        chop clean-up
        chop purge

    A message qualifies for removal when either:
        1. It was posted by this bot, or
        2. Its text starts with "chop " case-insensitively.

    The prefix check intentionally behaves like SQL `LIKE 'chop %'` rather
    than validating command syntax. Malformed attempts are therefore still
    found, while unrelated words such as "chopper" are left alone.

    The cleanup is limited to the channel where the command is issued.
    Individual deletes are used so older messages can also be removed.
    """

    if message.guild is None:
        await message.channel.send(
            "Cleanup can only be run inside a server channel."
        )
        return

    # Cleanup can remove many messages, so restrict it to members who already
    # have Discord's Manage Messages permission in this channel.
    user_permissions = message.channel.permissions_for(message.author)

    if not user_permissions.manage_messages:
        await message.channel.send(
            "You need the Manage Messages permission to use Chopper cleanup.",
            delete_after=12,
        )
        return

    if message.guild.me is None:
        await message.channel.send(
            "I could not determine my server permissions.",
            delete_after=12,
        )
        return

    bot_permissions = message.channel.permissions_for(message.guild.me)

    if not bot_permissions.manage_messages:
        await message.channel.send(
            "I need Manage Messages permission in this channel to clean up posts.",
            delete_after=12,
        )
        return

    bot_user_id = client.user.id if client.user else None

    deleted_bot_posts = 0
    deleted_chop_posts = 0
    failed_deletes = 0

    async for history_message in message.channel.history(
        limit=None,
        oldest_first=False,
    ):
        is_chopper_post = (
            bot_user_id is not None
            and history_message.author.id == bot_user_id
        )

        # LIKE-style "chop %" match. This finds messages whose content starts
        # with "chop " (case-insensitively), including malformed commands that
        # failed to activate Chopper. It does not match ordinary words such as
        # "chopper", "chopping", or messages where "chop" appears later.
        contains_chop = history_message.content.casefold().startswith("chop ")

        if not is_chopper_post and not contains_chop:
            continue

        try:
            await history_message.delete()

            if is_chopper_post:
                deleted_bot_posts += 1
            else:
                deleted_chop_posts += 1

        except (discord.Forbidden, discord.HTTPException):
            failed_deletes += 1

    # The cleanup command itself is normally deleted by the scan because it
    # contains "chop". Leave only a temporary completion summary.
    summary = (
        "**Chopper Cleanup Complete**\n\n"
        f"Chopper posts removed: {deleted_bot_posts}\n"
        f"User posts matching `chop %` removed: {deleted_chop_posts}\n"
        f"Failed deletions: {failed_deletes}"
    )

    await message.channel.send(
        summary,
        delete_after=CLEANUP_SUMMARY_SECONDS,
    )


# ---------------------------------------------------------------------------
# Discord Events
# ---------------------------------------------------------------------------

@client.event
async def on_ready() -> None:
    """Runs when the bot successfully logs in or reconnects."""

    global _startup_permission_check_complete

    print(f"Chopper v{BOT_VERSION} logged in as {client.user}")

    # on_ready() can run again after a reconnect. The permission report only
    # needs to be printed once per process start.
    if not _startup_permission_check_complete:
        await check_permissions_on_startup()
        _startup_permission_check_complete = True


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