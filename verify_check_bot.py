"""
ELT Verify-Check Bot  (v2 - embed edition)
==========================================
Staff type /verifycard and the bot posts a clean embed card in VERIFY-CHECK:

    NAME / AGE / INVITE BY / USER   + the staff roles pinged above the embed
    (the bot adds NO reactions - staff react themselves)

How the inviter is found (in this order):
    1. The InviteLogger message in the -INVITE channel
       ("X just joined. They were invited by Y who now has N invites").
       Matched by the member's ID / mention first, then by exact name. Only the
       "X just joined" part is checked, so the INVITER's messages are never
       mistaken for the member's. The newest matching message wins.
    2. The bot's own invite tracker (works for members who joined while the bot was online;
       needs the Manage Server permission, otherwise it is silently skipped).
    3. The optional `invited_by` option of the command.
    4. `Unknown`.

Requirements:
    pip install "discord.py>=2.4"

Before running:
    - Developer Portal: enable SERVER MEMBERS INTENT and MESSAGE CONTENT INTENT.
    - Bot permissions: View Channels, Send Messages, Embed Links, Read Message History,
      Mention @everyone/@here/All Roles (to ping staff roles). It must SEE -INVITE and VERIFY-CHECK.
      (Manage Server is optional, only for the invite tracker.)
    - Set DISCORD_TOKEN as an environment variable (Railway > Variables).
"""

from __future__ import annotations

import asyncio
import os
import re
import unicodedata

import discord
from discord import app_commands
from discord.ext import commands

# =========================== CONFIG ===========================
TOKEN = os.environ.get("DISCORD_TOKEN")

GUILD_ID = 1410440666747633707  # ELT server ID

# Card channel: found by name, or paste the channel ID to force it.
VERIFY_CHECK_CHANNEL_ID: int | None = None
VERIFY_CHECK_NAME = "verify-check"

# Roles that can use /verifycard and get pinged on every card.
STAFF_ROLE_ID = 1513904127837736992
ADMIN_ROLE_ID = 1513904120803889243
UT_STAFF_ROLE_NAME = "ut staff"
UT_STAFF_ROLE_ID: int | None = None

# InviteLogger channel: found by name, or paste the channel ID to force it.
INVITE_CHANNEL_ID: int | None = None
INVITE_CHANNEL_NAME = "invite"
INVITE_HISTORY_LIMIT = 1500  # how many recent messages of that channel are searched

EMBED_COLOR = 0x8B2BE2
SERVER_FOOTER = "ELT | ELITE LEADERS COMMUNITY"
# ================================================================

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.invites = True

bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)


# ----------------------------------------------------------------- text helpers
def norm(text: str) -> str:
    """Ignores fancy letters, emoji and symbols: '✅┃𝗩𝗘𝗥𝗜𝗙𝗬-𝗖𝗛𝗘𝗖𝗞' -> 'verifycheck'."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    return "".join(c for c in text if c.isalnum())


def clean(text: str) -> str:
    return (text or "").replace("*", "").replace("`", "").replace("\\", "").strip()


def safe_text(text: str) -> str:
    return discord.utils.escape_mentions(discord.utils.escape_markdown(text))


# ----------------------------------------------------------------- channel / role lookup
def find_text_channel(guild: discord.Guild, forced_id: int | None, name: str, exact: bool) -> discord.TextChannel | None:
    if forced_id:
        ch = guild.get_channel(forced_id)
        return ch if isinstance(ch, discord.TextChannel) else None
    key = norm(name)
    # exact match first, then "contains" match
    for ch in guild.text_channels:
        if norm(ch.name) == key:
            return ch
    if not exact:
        for ch in guild.text_channels:
            if key in norm(ch.name):
                return ch
    return None


def find_verify_channel(guild: discord.Guild) -> discord.TextChannel | None:
    return find_text_channel(guild, VERIFY_CHECK_CHANNEL_ID, VERIFY_CHECK_NAME, exact=False)


def find_invite_channel(guild: discord.Guild) -> discord.TextChannel | None:
    return find_text_channel(guild, INVITE_CHANNEL_ID, INVITE_CHANNEL_NAME, exact=True)


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


# ----------------------------------------------------------------- reading the -INVITE channel
# "<joiner> just joined. They were invited by <inviter> who now has N invites"
INVITED_BY_RE = re.compile(
    r"invited\s+by\s+(.+?)(?=\s+who\s+now\s+has|\s*\(|\s*\n|\s*$)",
    re.IGNORECASE | re.DOTALL,
)
JOINED_TAIL_RE = re.compile(r"\s*(?:just\s+|has\s+)*joined.*$", re.IGNORECASE | re.DOTALL)
MENTION_RE = re.compile(r"<@!?(\d+)>")


def message_text(msg: discord.Message) -> str:
    """Everything readable in a message: content + all embed parts."""
    parts: list[str] = [msg.content or ""]
    for e in msg.embeds:
        parts += [e.title or "", e.description or ""]
        if e.author and e.author.name:
            parts.append(e.author.name)
        for f in e.fields:
            parts += [f.name or "", f.value or ""]
        if e.footer and e.footer.text:
            parts.append(e.footer.text)
    return "\n".join(p for p in parts if p)


def split_invite_text(text: str) -> tuple[str, str] | None:
    """Returns (joiner_part, inviter_raw) or None if this is not an invite message."""
    m = INVITED_BY_RE.search(text)
    if not m:
        return None
    joiner = text[: m.start()]
    inviter = clean(m.group(1)).strip(" .,:;-")
    if not inviter:
        return None
    return joiner, inviter


def member_name_keys(member: discord.Member) -> set[str]:
    keys = {norm(member.name), norm(member.display_name)}
    if member.global_name:
        keys.add(norm(member.global_name))
    keys.discard("")
    return keys


def joiner_matches_id(joiner: str, member: discord.Member) -> bool:
    for found in MENTION_RE.findall(joiner):
        if int(found) == member.id:
            return True
    return re.search(rf"(?<!\d){member.id}(?!\d)", joiner) is not None


def joiner_matches_name(joiner: str, member: discord.Member) -> bool:
    lines = [ln for ln in clean(joiner).splitlines() if ln.strip()]
    if not lines:
        return False
    name = JOINED_TAIL_RE.sub("", lines[-1]).strip().lstrip("@")
    key = norm(name)
    return bool(key) and key in member_name_keys(member)


async def find_inviter_raw(guild: discord.Guild, member: discord.Member) -> str | None:
    """Searches the -INVITE channel newest -> oldest. An ID/mention match wins over a name match."""
    ch = find_invite_channel(guild)
    if ch is None:
        return None
    name_fallback: str | None = None
    try:
        async for msg in ch.history(limit=INVITE_HISTORY_LIMIT):
            parsed = split_invite_text(message_text(msg))
            if parsed is None:
                continue
            joiner, inviter = parsed
            if joiner_matches_id(joiner, member):
                return inviter
            if name_fallback is None and joiner_matches_name(joiner, member):
                name_fallback = inviter
    except discord.HTTPException:
        return name_fallback
    return name_fallback


async def inviter_to_text(guild: discord.Guild, raw: str) -> tuple[str, int | None]:
    """Turns the inviter text from the log into a real mention when possible."""
    m = MENTION_RE.search(raw)
    if m:
        return f"<@{m.group(1)}>", int(m.group(1))

    name = raw.strip().lstrip("@").strip()
    key = norm(name)
    low = name.lower()

    for mem in guild.members:
        if low in (mem.name.lower(), (mem.global_name or "").lower(), mem.display_name.lower()):
            return mem.mention, mem.id
    if key:
        for mem in guild.members:
            if key in {norm(mem.name), norm(mem.display_name), norm(mem.global_name or "")}:
                return mem.mention, mem.id
    try:
        for mem in await guild.query_members(query=name[:100], limit=5):
            if low in (mem.name.lower(), (mem.global_name or "").lower(), mem.display_name.lower()):
                return mem.mention, mem.id
    except (discord.HTTPException, asyncio.TimeoutError):
        pass
    # Inviter left the server / can't be matched: show the name as plain text.
    return f"**{safe_text(name)}**", None


# ----------------------------------------------------------------- own invite tracker (fallback)
invite_cache: dict[str, int] = {}
join_inviter: dict[int, int] = {}  # member id -> inviter id


async def refresh_invites(guild: discord.Guild) -> list[discord.Invite]:
    try:
        invites = await guild.invites()
    except (discord.Forbidden, discord.HTTPException):
        return []
    invite_cache.clear()
    invite_cache.update({i.code: (i.uses or 0) for i in invites})
    return invites


@bot.event
async def on_member_join(member: discord.Member):
    if member.guild.id != GUILD_ID:
        return
    old = dict(invite_cache)
    invites = await refresh_invites(member.guild)
    used = [i for i in invites if (i.uses or 0) > old.get(i.code, 0) and i.inviter]
    if len(used) == 1:
        join_inviter[member.id] = used[0].inviter.id


@bot.event
async def on_invite_create(invite: discord.Invite):
    if invite.guild and invite.guild.id == GUILD_ID:
        invite_cache[invite.code] = invite.uses or 0


@bot.event
async def on_invite_delete(invite: discord.Invite):
    invite_cache.pop(invite.code, None)


# ----------------------------------------------------------------- events
@bot.event
async def setup_hook():
    guild = discord.Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    try:
        await bot.tree.sync(guild=guild)
    except discord.HTTPException as e:
        print(f"⚠️ Command sync failed: {e}")


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

    await refresh_invites(guild)
    print(f"ℹ️ Invite tracker: {len(invite_cache)} invites cached" if invite_cache
          else "ℹ️ Invite tracker off (needs Manage Server) — InviteLogger lookup still works")

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

    ch = find_verify_channel(guild)
    if ch is None:
        print("❌ Can't find the VERIFY-CHECK channel — set VERIFY_CHECK_CHANNEL_ID at the top of the file")
    else:
        perms = ch.permissions_for(guild.me)
        missing = [n for n, ok in (
            ("View Channel", perms.view_channel),
            ("Send Messages", perms.send_messages),
            ("Embed Links", perms.embed_links),
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

    # ---- who invited? ----
    invite_text: str | None = None
    if invited_by is not None:
        invite_text = invited_by.mention
    else:
        raw = await find_inviter_raw(guild, member)
        if raw:
            invite_text, _ = await inviter_to_text(guild, raw)
        elif member.id in join_inviter:
            invite_text = f"<@{join_inviter[member.id]}>"
    found = invite_text is not None
    if not found:
        invite_text = "`Unknown`"

    # ---- staff pings ----
    roles = [r for r in (guild.get_role(STAFF_ROLE_ID), find_ut_staff_role(guild)) if r is not None]
    pings = " ".join(r.mention for r in roles)

    # ---- the embed ----
    embed = discord.Embed(
        title="✦ VERIFICATION CARD ✦",
        color=EMBED_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    embed.description = (
        f"## NAME : {safe_text(str(name))}\n"
        f"## AGE : {age}\n"
        f"## INVITE BY : {invite_text}\n"
        f"## USER : {member.mention}"
    )
    embed.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
    if member.joined_at:
        embed.add_field(name="Joined server", value=discord.utils.format_dt(member.joined_at, "R"), inline=True)
    embed.add_field(name="Checked by", value=interaction.user.mention, inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=SERVER_FOOTER)

    try:
        await channel.send(
            content=pings or None,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=roles or False),
        )
    except discord.Forbidden:
        await interaction.followup.send(
            f"❌ I can't send messages / embeds in {channel.mention} (check my permissions there).", ephemeral=True
        )
        return
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ Discord error: {e}", ephemeral=True)
        return

    note = "" if found else "\n⚠️ I couldn't find them in the -INVITE channel — use the `invited_by` option next time."
    await interaction.followup.send(f"✅ Card posted in {channel.mention}.{note}", ephemeral=True)


if not TOKEN:
    raise SystemExit("DISCORD_TOKEN is not set. Add it in Railway's Variables tab, then redeploy.")

bot.run(TOKEN)
