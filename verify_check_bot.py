"""
ELT Verify-Check Bot
=======================================
Staff type  /verifycard  and the bot posts the finished card in VERIFY-CHECK:

    # NAME : ahmed
    # AGE : 17
    # INVITE BY : @haythem        <- read from the InviteLogger messages in -INVITE
    # USER : @morty
    @UT STAFF @STAFF              <- both staff roles are pinged
    (reactions ✅ ❌ added under it)

Staff only fill in the member, the name and the age. The bot finds who invited the member by
reading the InviteLogger message in the -INVITE channel. If it can't find one, it writes
`Unknown` - staff can then fill the optional "invited_by" option themselves.

Requirements:
    pip install "discord.py>=2.4"

Before running:
    - Enable SERVER MEMBERS INTENT and MESSAGE CONTENT INTENT for the bot in the Developer Portal
      (the bot needs the second one to read the InviteLogger messages).
    - Invite the bot with: View Channels, Send Messages, Add Reactions, Read Message History and
      Mention @everyone/@here/All Roles (to ping the staff roles). It must be able to SEE the
      -INVITE channel and the VERIFY-CHECK channel.
    - Set DISCORD_TOKEN as an environment variable (Railway > Variables).
"""

from __future__ import annotations

import os
import re
import unicodedata

import discord
from discord import app_commands
from discord.ext import commands

# =========================== CONFIG ===========================
TOKEN = os.environ.get("DISCORD_TOKEN")

GUILD_ID = 1410440666747633707  # ELT server ID

# The channel the cards are posted in. It is found automatically by its name ("VERIFY-CHECK",
# emoji / symbols are ignored). To force it, paste the channel ID here instead of None.
VERIFY_CHECK_CHANNEL_ID: int | None = None
VERIFY_CHECK_NAME = "verify-check"

# Roles that can use /verifycard and that get pinged on every card.
STAFF_ROLE_ID = 1513904127837736992
ADMIN_ROLE_ID = 1513904120803889243
UT_STAFF_ROLE_NAME = "ut staff"   # found by name (set UT_STAFF_ROLE_ID below to force it)
UT_STAFF_ROLE_ID: int | None = None

# Reactions the bot adds under each card.
REACTIONS = ["✅", "❌"]

# The channel where the InviteLogger bot posts "X just joined. They were invited by Y ...".
# Found automatically by its name ("invite"); paste the channel ID to force it.
INVITE_CHANNEL_ID: int | None = None
INVITE_CHANNEL_NAME = "invite"
# How many recent messages of that channel to search for the member.
INVITE_HISTORY_LIMIT = 500
# ================================================================

intents = discord.Intents.default()
intents.members = True           # to find members by name (enable in the Developer Portal)
intents.message_content = True   # to read the InviteLogger messages (enable in the Developer Portal)

bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)


def norm(text: str) -> str:
    """Ignores fancy letters, emoji and symbols: '✅┃𝗩𝗘𝗥𝗜𝗙𝗬-𝗖𝗛𝗘𝗖𝗞' -> 'verifycheck'."""
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(c for c in text if c.isalnum())


# ----------------------------------------------------------------- reading the -INVITE channel
INVITED_BY_RE = re.compile(r"invited by\s+(.+?)\s+who now has", re.IGNORECASE | re.DOTALL)


def clean(text: str) -> str:
    return text.replace("*", "").replace("`", "").replace("\\", "").strip()


def find_invite_channel(guild: discord.Guild) -> discord.TextChannel | None:
    if INVITE_CHANNEL_ID:
        ch = guild.get_channel(INVITE_CHANNEL_ID)
        return ch if isinstance(ch, discord.TextChannel) else None
    key = norm(INVITE_CHANNEL_NAME)
    for ch in guild.text_channels:
        if norm(ch.name) == key:
            return ch
    return None


async def find_inviter_name(guild: discord.Guild, member: discord.Member) -> str | None:
    """Searches the -INVITE channel (newest first) for the message about this member."""
    ch = find_invite_channel(guild)
    if ch is None:
        return None
    id_text = str(member.id)
    try:
        async for msg in ch.history(limit=INVITE_HISTORY_LIMIT):
            texts = [msg.content] + [e.description or "" for e in msg.embeds]
            for text in texts:
                if id_text in text:
                    m = INVITED_BY_RE.search(text)
                    if m:
                        return clean(m.group(1))
    except discord.HTTPException:
        return None
    return None


def resolve_member(guild: discord.Guild, name: str) -> discord.Member | None:
    """Turns the inviter's name from the message into a real member (so it can be tagged)."""
    n = name.lower()
    for m in guild.members:
        if n in (m.name.lower(), (m.global_name or "").lower(), m.display_name.lower()):
            return m
    return None


# ----------------------------------------------------------------- helpers
def find_verify_channel(guild: discord.Guild) -> discord.TextChannel | None:
    if VERIFY_CHECK_CHANNEL_ID:
        ch = guild.get_channel(VERIFY_CHECK_CHANNEL_ID)
        return ch if isinstance(ch, discord.TextChannel) else None
    key = norm(VERIFY_CHECK_NAME)
    for ch in guild.text_channels:
        if key in norm(ch.name):
            return ch
    return None


def find_ut_staff_role(guild: discord.Guild) -> discord.Role | None:
    if UT_STAFF_ROLE_ID:
        return guild.get_role(UT_STAFF_ROLE_ID)
    key = norm(UT_STAFF_ROLE_NAME)
    for r in guild.roles:
        if norm(r.name) == key:
            return r
    return None


def is_staff(member: discord.abc.User) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True
    ids = {r.id for r in member.roles}
    ut = find_ut_staff_role(member.guild)
    return STAFF_ROLE_ID in ids or ADMIN_ROLE_ID in ids or (ut is not None and ut.id in ids)


# ----------------------------------------------------------------- events
@bot.event
async def setup_hook():
    guild = discord.Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)


_ready_done = False


@bot.event
async def on_ready():
    global _ready_done
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    if _ready_done:
        return
    _ready_done = True

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        print(f"❌ The bot is not in the server {GUILD_ID} — check GUILD_ID and that the bot was invited")
        return

    # Check the -INVITE channel (where InviteLogger posts).
    inv = find_invite_channel(guild)
    if inv is None:
        print("❌ Can't find the -INVITE channel — set INVITE_CHANNEL_ID at the top of the file")
    else:
        p = inv.permissions_for(guild.me)
        if p.view_channel and p.read_message_history:
            print(f"✅ Reading invites from #{inv.name}")
        else:
            print(f"⚠️ I can't read #{inv.name} — give the bot View Channel + Read Message History there")
    if not bot.intents.message_content:
        print("⚠️ Message Content intent is off")

    # Check the channel + roles.
    ch = find_verify_channel(guild)
    if ch is None:
        print("❌ Can't find the VERIFY-CHECK channel — set VERIFY_CHECK_CHANNEL_ID at the top of the file")
    else:
        perms = ch.permissions_for(guild.me)
        missing = [n for n, ok in (
            ("View Channel", perms.view_channel),
            ("Send Messages", perms.send_messages),
            ("Add Reactions", perms.add_reactions),
        ) if not ok]
        print(f"✅ Cards go to #{ch.name}" + (f"  ⚠️ missing: {', '.join(missing)}" if missing else ""))
    if guild.get_role(STAFF_ROLE_ID) is None:
        print(f"⚠️ STAFF role {STAFF_ROLE_ID} not found")
    ut = find_ut_staff_role(guild)
    print(f"✅ UT STAFF role: @{ut.name}" if ut else "⚠️ UT STAFF role not found — only @STAFF will be pinged")


# ----------------------------------------------------------------- the command
@bot.tree.command(name="verifycard", description="Post the verification card in VERIFY-CHECK (staff only)")
@app_commands.describe(
    member="The member being verified",
    name="Their name",
    age="Their age",
    invited_by="Only if the bot says Unknown: who invited them",
)
async def verifycard(
    interaction: discord.Interaction,
    member: discord.Member,
    name: app_commands.Range[str, 1, 50],
    age: app_commands.Range[int, 5, 99],
    invited_by: discord.Member | None = None,
):
    if not is_staff(interaction.user):
        await interaction.response.send_message("❌ Only staff can use this command.", ephemeral=True)
        return
    guild = interaction.guild
    if guild is None or guild.id != GUILD_ID:
        await interaction.response.send_message("❌ Use this in the ELT server.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    channel = find_verify_channel(guild)
    if channel is None:
        await interaction.followup.send("❌ I can't find the VERIFY-CHECK channel.", ephemeral=True)
        return

    # Who invited?
    if invited_by is not None:
        invite_text = invited_by.mention
    else:
        found = await find_inviter_name(guild, member)
        if found:
            inviter = resolve_member(guild, found)
            invite_text = inviter.mention if inviter else discord.utils.escape_mentions(discord.utils.escape_markdown(found))
        else:
            invite_text = "`Unknown`"

    # Staff roles to ping.
    roles = [r for r in (guild.get_role(STAFF_ROLE_ID), find_ut_staff_role(guild)) if r is not None]
    pings = " ".join(r.mention for r in roles)

    content = (
        f"# NAME : {discord.utils.escape_markdown(str(name))}\n"
        f"# AGE : {age}\n"
        f"# INVITE BY : {invite_text}\n"
        f"# USER : {member.mention}\n"
        f"{pings}"
    )
    try:
        msg = await channel.send(
            content,
            allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=roles or False),
        )
    except discord.Forbidden:
        await interaction.followup.send(
            f"❌ I can't send messages in {channel.mention} (check my permissions there).", ephemeral=True
        )
        return
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ Discord error: {e}", ephemeral=True)
        return

    for emoji in REACTIONS:
        try:
            await msg.add_reaction(emoji)
        except discord.HTTPException:
            break

    note = "" if invite_text != "`Unknown`" else "\n⚠️ I couldn't find them in the -INVITE channel — you can use the `invited_by` option next time."
    await interaction.followup.send(f"✅ Card posted in {channel.mention}.{note}", ephemeral=True)


if not TOKEN:
    raise SystemExit("DISCORD_TOKEN is not set. Add it in Railway's Variables tab, then redeploy.")

bot.run(TOKEN)
