import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import os
import sys
import time
import asyncio
import json
from collections import defaultdict
import logging
import threading

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("discord_bot")

# Load environment variables
load_dotenv()

# Initialize intents
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True

# Initialize bot
bot = commands.Bot(command_prefix='!', intents=intents)

# Get absolute path for data files
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_FILE = os.path.join(SCRIPT_DIR, "reports.json")
PRISON_STATE_FILE = os.path.join(SCRIPT_DIR, "prison_state.json")
DM_PERMISSIONS_FILE = os.path.join(SCRIPT_DIR, "dm_permissions.json")

# Constants
LOG_CHANNEL_IDS = [1351561404150448248, 1350543441821564988]
PRISON_DURATION = 3600  # 1 hour
VOTE_DURATION = 300  # 5 minutes
VOTE_COOLDOWN = 900  # 15 minutes
PRISON_ROLE_NAME = "Prisoner"
ADMIN_USER_IDS = [776744923738800129]
MOD_ROLE_NAME = "Moderator"  # Added role for moderators
REQUIRED_VOTES = 3
REPORT_COOLDOWN = 3600  # 1 hour cooldown between reports

# Voting system variables
vote_sessions = {}  # Structure: {prisoner_id: {'voters': set(), 'message_id': None}}
vote_cooldowns = {}  # Structure: {voter_id: timestamp}
# Data storage variables

reported_users = defaultdict(lambda: {
    'count': 0,
    'reasons': [],
    'last_report': 0
})
reporter_cooldowns = defaultdict(dict)  # Structure: {reporter_id: {reported_id: timestamp}}
user_roles_before_prison = {}
user_nicknames_before_prison = {}
vote_sessions = {}
vote_cooldowns = {}
dm_permissions = defaultdict(list)  # Structure: {reported_id: [reporter_ids allowed to DM]}

# Lock for file operations to prevent race conditions
file_lock = threading.Lock()

def ensure_directory_exists():
    """Ensure the data directory exists"""
    os.makedirs(SCRIPT_DIR, exist_ok=True)

def save_data(data, file_path, default_factory=None):
    """Generic function to save data to file with locking"""
    with file_lock:
        ensure_directory_exists()
        try:
            with open(file_path, "w") as f:
                if default_factory:
                    json.dump(dict(data), f, indent=4)
                else:
                    json.dump(data, f, indent=4)
            return True
        except Exception as e:
            logger.error(f"Error saving to {file_path}: {e}")
            return False

def load_data(file_path, default_factory=None):
    """Generic function to load data from file"""
    try:
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                data = json.load(f)
                if default_factory:
                    return defaultdict(default_factory, data)
                return data
        else:
            if default_factory:
                return defaultdict(default_factory)
            return {}
    except Exception as e:
        logger.error(f"Error loading from {file_path}: {e}")
        if default_factory:
            return defaultdict(default_factory)
        return {}

def save_reports():
    """Save reported users data"""
    return save_data(reported_users, REPORTS_FILE, lambda: {
        'count': 0,
        'reasons': [],
        'last_report': 0
    })

def load_reports():
    """Load reported users data"""
    global reported_users
    reported_users = load_data(REPORTS_FILE, lambda: {
        'count': 0,
        'reasons': [],
        'last_report': 0
    })

def save_prison_state():
    """Save prison state data"""
    try:
        prison_data = {
            "user_roles": {
                str(user_id): [role.id for role in roles]
                for user_id, roles in user_roles_before_prison.items()
            },
            "user_nicknames": user_nicknames_before_prison,
            "imprisonment_times": {
                str(user_id): time.time()
                for user_id in user_roles_before_prison
            }
        }
        return save_data(prison_data, PRISON_STATE_FILE)
    except Exception as e:
        logger.error(f"Error preparing prison state data: {e}")
        return False

def load_prison_state():
    """Load prison state data"""
    global user_roles_before_prison, user_nicknames_before_prison
    try:
        prison_data = load_data(PRISON_STATE_FILE)
        if prison_data:
            user_nicknames_before_prison = prison_data.get("user_nicknames", {})
            user_roles_before_prison = {}  # Initialize as normal dict
            return prison_data
        return None
    except Exception as e:
        logger.error(f"Error loading prison state: {e}")
        return None

def save_dm_permissions():
    """Save DM permissions data"""
    return save_data(dm_permissions, DM_PERMISSIONS_FILE, list)

def load_dm_permissions():
    """Load DM permissions data"""
    global dm_permissions
    dm_permissions = load_data(DM_PERMISSIONS_FILE, list)

async def put_in_prison(member):
    """Put a member in prison"""
    if not member:
        return False
    
    try:
        guild = member.guild
        prison_role = discord.utils.get(guild.roles, name=PRISON_ROLE_NAME)
        
        # Create prison role if it doesn't exist
        if not prison_role:
            try:
                prison_role = await guild.create_role(
                    name=PRISON_ROLE_NAME,
                    permissions=discord.Permissions.none()
                )
                
                # Configure permissions for all channels
                for channel in guild.channels:
                    await channel.set_permissions(prison_role, 
                                               send_messages=False,
                                               add_reactions=False)
            except discord.errors.Forbidden:
                await log_activity(f"❌ Failed to create prison role - insufficient permissions")
                return False
        
        # Store original roles and nickname
        user_roles_before_prison[member.id] = member.roles
        user_nicknames_before_prison[str(member.id)] = member.display_name
        
        # Apply prison role
        try:
            new_nick = f"🔒 Prisoner"
            if len(new_nick) > 32:
                new_nick = new_nick[:32]
            await member.edit(roles=[prison_role], nick=new_nick)
            await log_activity(f"🔒 {member.mention} has been imprisoned for 1 hour!")
            
            # Save state
            save_prison_state()
            
            # Schedule release
            asyncio.create_task(release_after_delay(member, PRISON_DURATION))
            return True
        except discord.errors.Forbidden:
            await log_activity(f"❌ Failed to imprison {member.mention} - insufficient permissions")
            return False
    except Exception as e:
        await log_activity(f"❌ Error imprisoning {member.mention}: {e}")
        logger.error(f"Error in put_in_prison: {e}")
        return False

async def release_from_prison(member):
    """Release a member from prison"""
    if not member or member.id not in user_roles_before_prison:
        return False
    
    try:
        # Restore original roles
        original_roles = user_roles_before_prison.pop(member.id, [])
        if not original_roles:
            # If no roles to restore, just remove prison role
            prison_role = discord.utils.get(member.guild.roles, name=PRISON_ROLE_NAME)
            if prison_role and prison_role in member.roles:
                await member.remove_roles(prison_role)
        else:
            await member.edit(roles=original_roles)
        
        # Restore nickname
        original_nick = user_nicknames_before_prison.pop(str(member.id), None)
        if original_nick:
            try:
                await member.edit(nick=original_nick)
            except discord.errors.Forbidden:
                pass  # Ignore nickname errors
        
        # Save updated state
        save_prison_state()
        
        await log_activity(f"🔓 {member.mention} has been released from prison!")
        return True
    except discord.errors.Forbidden:
        await log_activity(f"❌ Failed to release {member.mention} - insufficient permissions")
        return False
    except Exception as e:
        await log_activity(f"❌ Error releasing {member.mention}: {e}")
        logger.error(f"Error in release_from_prison: {e}")
        return False

async def release_after_delay(member, delay):
    """Release member after a delay"""
    await asyncio.sleep(delay)
    await release_from_prison(member)

async def log_activity(message):
    """Log activity to designated channel(s)"""
    for channel_id in LOG_CHANNEL_IDS:
        channel = bot.get_channel(channel_id)
        if channel:
            try:
                await channel.send(message)
            except discord.errors.HTTPException as e:
                logger.error(f"Error sending log message: {e}")
        else:
            logger.warning(f"Log channel {channel_id} not found")

# Event handlers
@bot.event
async def on_ready():
    """Bot initialization when ready"""
    load_reports()
    load_dm_permissions()
    # Replace the emoji with a simple text alternative for console output
    print(f'Bot {bot.user} is now online!')
    # Keep the emoji for the Discord log since Discord supports it
    await log_activity(f'✅ **Bot {bot.user} is back online after restart!**')

    # Restore prison state
    prison_data = load_prison_state()
    await restore_prison_state(prison_data)
    
    # Start background tasks
    check_prison_releases.start()

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ **Perintah tidak lengkap! Gunakan `!help {ctx.command}` untuk melihat cara penggunaan.**")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ **Invalid argument: {error}**")
    elif isinstance(error, commands.CommandNotFound):
        pass  # Abaikan perintah yang tidak ditemukan
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ **User tidak ditemukan!**")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ **Anda tidak memiliki izin untuk menggunakan perintah ini!**")
    else:
        logger.error(f"Command error: {error}")
        await ctx.send(f"❌ **Terjadi kesalahan: {str(error)}**")

def is_mod_or_admin(ctx):
    """Check if user is mod or admin"""
    if ctx.author.id in ADMIN_USER_IDS:
        return True
    
    mod_role = discord.utils.get(ctx.guild.roles, name=MOD_ROLE_NAME)
    return mod_role in ctx.author.roles if mod_role else False

@bot.command()
async def openreport(ctx, reported_user: discord.Member = None, *, reason=None):
    """Report a user for misconduct"""
    if not reported_user or not reason:
        await ctx.send("❌ **Use the correct format:** `!openreport @user reason for report`")
        return

    # Don't allow self-reports
    if reported_user.id == ctx.author.id:
        await ctx.send("❌ **You cannot report yourself!**")
        return

    # Don't allow reporting bots
    if reported_user.bot:
        await ctx.send("❌ **You cannot report bots!**")
        return
    
    # Don't allow reporting admins/mods
    if reported_user.id in ADMIN_USER_IDS or discord.utils.get(ctx.guild.roles, name=MOD_ROLE_NAME) in reported_user.roles:
        await ctx.send("❌ **You cannot report moderators or administrators!**")
        return

    reporter_id = str(ctx.author.id)
    reported_id = str(reported_user.id)
    current_time = time.time()

    # Check cooldown based on reporter and reported user
    if reported_id in reporter_cooldowns.get(reporter_id, {}) and current_time - reporter_cooldowns[reporter_id][reported_id] < REPORT_COOLDOWN:
        remaining_time = REPORT_COOLDOWN - (current_time - reporter_cooldowns[reporter_id][reported_id])
        await ctx.send(f"❌ **You can only report {reported_user.mention} once per hour! Time remaining: {remaining_time/60:.1f} minutes.**")
        return

    # Update cooldown
    if reporter_id not in reporter_cooldowns:
        reporter_cooldowns[reporter_id] = {}
    reporter_cooldowns[reporter_id][reported_id] = current_time

    # Initialize if not exists
    if reported_id not in reported_users:
        reported_users[reported_id] = {
            'count': 0,
            'reasons': [],
            'last_report': 0
        }

    reported_users[reported_id]['count'] += 1
    reported_users[reported_id]['reasons'].append(f"{ctx.author.name}: {reason}")
    reported_users[reported_id]['last_report'] = current_time
    save_reports()

    await ctx.send(f"🚨 **Report received against {reported_user.mention}:** {reason}")
    await log_activity(f"📢 **{ctx.author.mention} reported {reported_user.mention}**\n📝 **Reason:** {reason}")

    # Actions based on report count
    count = reported_users[reported_id]['count']
    if count == 2:
        await ctx.send(f"⚠️ **NO NO YA {reported_user.mention}!** Reason: {reason}")
    elif count == 3:
        # Add reporter to DM permission list
        if reporter_id not in dm_permissions[reported_id]:
            dm_permissions[reported_id].append(reporter_id)
            save_dm_permissions()

        await ctx.send(f"✅ **Report sent successfully!** {ctx.author.mention}, want to DM {reported_user.mention}? Use `!dm {reported_user.id} message` to send a private message.")
    elif count >= 5 and count < 10:
        await ctx.send(f"⚠️ **{reported_user.mention} has received {count} reports. Reaching 10 will result in automatic imprisonment.**")
    elif count >= 10:
        success = await put_in_prison(reported_user)
        if success:
            # Reset to 1 report after imprisonment rather than 0
            reported_users[reported_id]['count'] = 1
            save_reports()
            await ctx.send(f"🔒 **{reported_user.mention} has been imprisoned for 1 hour due to receiving {count} reports.**")
        else:
            await ctx.send(f"⚠️ **Failed to imprison {reported_user.mention}. Please contact an administrator.**")

@bot.command()
@commands.check(is_mod_or_admin)
async def forceprison(ctx, member: discord.Member = None, *, reason=None):
    """Force a user into prison (Mod/Admin only)"""
    if not member:
        await ctx.send("❌ **Use the correct format:** `!forceprison @user [reason]`")
        return
    
    if member.id == ctx.author.id:
        await ctx.send("❌ **You cannot imprison yourself!**")
        return
    
    if member.bot:
        await ctx.send("❌ **You cannot imprison bots!**")
        return
    
    success = await put_in_prison(member)
    if success:
        await ctx.send(f"🔒 **{member.mention} has been forcibly imprisoned for 1 hour.**")
        if reason:
            await log_activity(f"🔒 **{ctx.author.mention} forcibly imprisoned {member.mention}**\n📝 **Reason:** {reason}")
        else:
            await log_activity(f"🔒 **{ctx.author.mention} forcibly imprisoned {member.mention}**")
    else:
        await ctx.send(f"❌ **Failed to imprison {member.mention}.**")

@bot.command()
@commands.check(is_mod_or_admin)
async def release(ctx, member: discord.Member = None):
    """Release a user from prison (Mod/Admin only)"""
    if not member:
        await ctx.send("❌ **Use the correct format:** `!release @user`")
        return
    
    success = await release_from_prison(member)
    if success:
        await ctx.send(f"🔓 **{member.mention} has been released from prison.**")
        await log_activity(f"🔓 **{ctx.author.mention} released {member.mention} from prison**")
    else:
        await ctx.send(f"❌ **Failed to release {member.mention} or they are not in prison.**")

@bot.command()
async def dm(ctx, user_id: int = None, *, message=None):
    """Send a DM to a reported user"""
    if not user_id or not message:
        await ctx.send("❌ **Use the correct format:** `!dm user_id message`")
        return
    
    reporter_id = str(ctx.author.id)
    reported_id = str(user_id)
    
    # Check if reporter has permission to DM
    if not is_mod_or_admin(ctx) and reporter_id not in dm_permissions.get(reported_id, []):
        await ctx.send("❌ **You don't have permission to DM this user.**")
        return
    
    # Get the user
    user = bot.get_user(user_id)
    if not user:
        try:
            user = await bot.fetch_user(user_id)
        except discord.errors.NotFound:
            await ctx.send("❌ **User not found.**")
            return
    
    # Send the DM
    try:
        await user.send(f"**Message from {ctx.author}:** {message}")
        await ctx.send(f"✅ **Message sent to {user}.**")
        await log_activity(f"📩 **{ctx.author.mention} sent a DM to {user.mention}**")
    except discord.errors.Forbidden:
        await ctx.send("❌ **Could not send message. The user might have DMs disabled.**")

@bot.command()
@commands.check(is_mod_or_admin)
async def clearreports(ctx, user: discord.Member = None):
    """Clear reports for a user (Mod/Admin only)"""
    if not user:
        await ctx.send("❌ **Use the correct format:** `!clearreports @user`")
        return
    
    user_id = str(user.id)
    if user_id in reported_users:
        old_count = reported_users[user_id]['count']
        reported_users[user_id] = {
            'count': 0,
            'reasons': [],
            'last_report': 0
        }
        save_reports()
        await ctx.send(f"✅ **Cleared {old_count} reports for {user.mention}.**")
        await log_activity(f"🧹 **{ctx.author.mention} cleared {old_count} reports for {user.mention}**")
    else:
        await ctx.send(f"✅ **{user.mention} has no reports.**")

@tasks.loop(minutes=5)
async def check_prison_releases():
    """Background task to check and release prisoners"""
    for guild in bot.guilds:
        prison_role = discord.utils.get(guild.roles, name=PRISON_ROLE_NAME)
        if prison_role:
            for member in guild.members:
                if prison_role in member.roles and member.id not in user_roles_before_prison:
                    # This member has the prison role but isn't in our tracking
                    # This could happen if the role was manually assigned
                    logger.info(f"Found member {member.name} with prison role but not in tracking")
    
    # No need to check for releases as they're scheduled individually

async def restore_prison_state(prison_data):
    """Restore prison state after bot restart"""
    if not prison_data:
        return

    for guild in bot.guilds:
        prison_role = discord.utils.get(guild.roles, name=PRISON_ROLE_NAME)
        if not prison_role:
            try:
                prison_role = await guild.create_role(
                    name=PRISON_ROLE_NAME,
                    permissions=discord.Permissions.none())
                    
                # Configure permissions for all channels
                for channel in guild.channels:
                    await channel.set_permissions(prison_role, 
                                               send_messages=False,
                                               add_reactions=False)
            except discord.errors.Forbidden:
                await log_activity(f"❌ Failed to create prison role - insufficient permissions")
                continue
                
        for user_id_str, role_ids in prison_data.get("user_roles", {}).items():
            user_id = int(user_id_str)
            member = guild.get_member(user_id)
            if member:
                # Check if prison time is already over
                imprisonment_time = prison_data.get("imprisonment_times", {}).get(user_id_str, 0)
                remaining_time = max(0, (imprisonment_time + PRISON_DURATION) - time.time())

                if remaining_time > 0:
                    # Save original roles - important: use role_ids loaded from file, not current roles
                    roles_to_restore = []
                    for role_id in role_ids:
                        role = guild.get_role(int(role_id))
                        if role:
                            roles_to_restore.append(role)
                    
                    # Update user_roles_before_prison with correct data
                    user_roles_before_prison[user_id] = roles_to_restore
                    
                    # Try to apply prison status
                    try:
                        await member.edit(roles=[prison_role])
                        await log_activity(
                            f"🔒 {member.mention} is still in prison after bot restart! Time remaining: {remaining_time/60:.1f} minutes."
                        )

                        # Schedule release
                        asyncio.create_task(release_after_delay(member, remaining_time))
                    except discord.errors.Forbidden:
                        # If we don't have permission, add 1 report instead
                        if user_id not in reported_users:
                            reported_users[user_id] = {
                                'count': 1,
                                'reasons': ["Automatic report due to permission issue"],
                                'last_report': time.time()
                            }
                        else:
                            reported_users[user_id]['count'] += 1
                            reported_users[user_id]['last_report'] = time.time()
                            if "Automatic report due to permission issue" not in reported_users[user_id]['reasons']:
                                reported_users[user_id]['reasons'].append("Automatic report due to permission issue")
                        
                        save_reports()  # Save report changes
                        await log_activity(f"⚠️ Cannot imprison {member.mention} - Added 1 report instead.")
                else:
                    # If time is up, release immediately
                    roles_to_restore = []
                    for role_id in role_ids:
                        role = guild.get_role(int(role_id))
                        if role:
                            roles_to_restore.append(role)
                    
                    try:
                        nickname = prison_data.get("user_nicknames", {}).get(user_id_str)
                        if nickname:
                            try:
                                await member.edit(nick=nickname)
                            except discord.errors.Forbidden:
                                pass  # Ignore nickname errors
                        
                        await member.edit(roles=roles_to_restore)
                        await log_activity(f"🔓 {member.mention} automatically released because imprisonment time has expired.")
                    except discord.errors.Forbidden:
                        # If we can't release, add report too
                        if user_id not in reported_users:
                            reported_users[user_id] = {
                                'count': 1,
                                'reasons': ["Automatic report due to release permission issue"],
                                'last_report': time.time()
                            }
                        else:
                            reported_users[user_id]['count'] += 1
                            reported_users[user_id]['last_report'] = time.time()
                            if "Automatic report due to release permission issue" not in reported_users[user_id]['reasons']:
                                reported_users[user_id]['reasons'].append("Automatic report due to release permission issue")
                        
                        save_reports()  # Save report changes
                        await log_activity(f"⚠️ Cannot release {member.mention} - Added 1 report instead.")




@bot.command()
async def voterelease(ctx, prisoner: discord.Member = None):
    if not prisoner:
        await ctx.send("❌ **Gunakan format yang benar:** `!voterelease @user`")
        return
    
    prison_role = discord.utils.get(ctx.guild.roles, name=PRISON_ROLE_NAME)
    if not prison_role or prison_role not in prisoner.roles:
        await ctx.send("❌ **User ini tidak sedang di penjara!**")
        return
    
    # Kode lainnya...
    if ctx.author.id in vote_cooldowns and time.time() - vote_cooldowns[ctx.author.id] < VOTE_COOLDOWN:
        remaining_time = VOTE_COOLDOWN - (time.time() - vote_cooldowns[ctx.author.id])
        await ctx.send(f"❌ **Anda harus menunggu {remaining_time/60:.1f} menit lagi sebelum membuat voting baru!**")
        return

    # Mulai voting baru
    vote_sessions[prisoner.id] = {'voters': set(), 'message_id': None}
    vote_cooldowns[ctx.author.id] = time.time()

    # Buat pesan voting dengan informasi yang jelas
    vote_msg = await ctx.send(
        f"🗳️ **Voting untuk mengeluarkan {prisoner.mention} dari penjara dimulai!**\n"
        f"👍 Ketik `!setuju` untuk memberikan suara.\n"
        f"⏱️ Waktu voting: 5 menit\n"
        f"🔢 Dibutuhkan minimal {REQUIRED_VOTES} suara.\n"
        f"0/{REQUIRED_VOTES} suara terkumpul.")

    vote_sessions[prisoner.id]['message_id'] = vote_msg.id

    # Tunggu selama durasi voting
    await asyncio.sleep(VOTE_DURATION)

    # Cek hasil voting
    if prisoner.id in vote_sessions:
        votes_count = len(vote_sessions[prisoner.id]['voters'])
        if votes_count >= REQUIRED_VOTES:
            await release_from_prison(prisoner)
            await ctx.send(f"✅ **{prisoner.mention} dibebaskan setelah voting berhasil dengan {votes_count} suara!**")
        else:
            await ctx.send(f"❌ **Voting gagal dengan {votes_count}/{REQUIRED_VOTES} suara. {prisoner.mention} tetap di penjara!**")

        # Hapus sesi voting
        del vote_sessions[prisoner.id]


@bot.command()
async def setuju(ctx, prisoner: discord.Member = None):
    # Kasus tanpa parameter - cek apakah hanya ada satu sesi voting aktif
    if not prisoner:
        active_sessions = []
        for prisoner_id, session in vote_sessions.items():
            # Pastikan prisoner tidak bisa vote untuk dirinya sendiri
            if ctx.author.id != prisoner_id:  
                active_sessions.append((prisoner_id, session))

        if not active_sessions:
            await ctx.send("❌ **Tidak ada voting yang sedang berlangsung!**")
            return

        if len(active_sessions) == 1:
            prisoner_id = active_sessions[0][0]
            prisoner = ctx.guild.get_member(prisoner_id)
        else:
            prisoners = [ctx.guild.get_member(pid) for pid, _ in active_sessions]
            prisoner_list = ", ".join([
                p.mention if p else f"<@{aid[0]}>"
                for p, aid in zip(prisoners, active_sessions)
            ])
            await ctx.send(f"❌ **Ada beberapa voting yang sedang berlangsung untuk: {prisoner_list}. Gunakan `!setuju @user` untuk memberikan suara ke user tertentu.**")
            return

    # Validasi parameter
    if not prisoner or prisoner.id not in vote_sessions:
        await ctx.send("❌ **Tidak ada voting yang sedang berlangsung untuk user tersebut!**")
        return

    # Cek apakah voter adalah prisoner itu sendiri
    if ctx.author.id == prisoner.id:
        await ctx.send("❌ **Anda tidak dapat memberikan suara untuk diri sendiri!**")
        return

    # Cek apakah sudah memberikan suara
    if ctx.author.id in vote_sessions[prisoner.id]['voters']:
        await ctx.send("❌ **Anda sudah memberikan suara pada voting ini!**")
        return

    # Tambahkan suara
    vote_sessions[prisoner.id]['voters'].add(ctx.author.id)
    votes_count = len(vote_sessions[prisoner.id]['voters'])

    # Update pesan voting jika bisa
    if vote_sessions[prisoner.id].get('message_id'):
        try:
            message = await ctx.channel.fetch_message(vote_sessions[prisoner.id]['message_id'])
            content = message.content.rsplit("\n", 1)[0]  # Hapus baris terakhir
            new_content = f"{content}\n{votes_count}/{REQUIRED_VOTES} suara terkumpul."
            await message.edit(content=new_content)
        except Exception as e:
            # Log error tapi jangan biarkan gagal
            print(f"Error updating vote message: {e}")

    await ctx.send(f"✅ **{ctx.author.mention} memberikan suara untuk membebaskan {prisoner.mention}! ({votes_count}/{REQUIRED_VOTES})**")
    
    # Periksa apakah jumlah suara sudah mencukupi untuk membebaskan
    if votes_count >= REQUIRED_VOTES:
        await release_from_prison(prisoner)
        await ctx.send(f"✅ **{prisoner.mention} dibebaskan karena jumlah suara telah mencukupi ({votes_count}/{REQUIRED_VOTES})!**")
        # Hapus sesi voting
        if prisoner.id in vote_sessions:
            del vote_sessions[prisoner.id]


@bot.command()
async def testreport(ctx, reported_user: discord.Member):
    if ctx.author.id not in ADMIN_USER_IDS:
        await ctx.send("❌ **Anda tidak memiliki izin untuk menggunakan perintah ini!**")
        return

    reported_id = str(reported_user.id)

    # Inisialisasi data report jika belum ada
    if reported_id not in reported_users:
        reported_users[reported_id] = {
            'count': 0,
            'reasons': [],
            'last_report': 0
        }

    reported_users[reported_id]['count'] = 15
    reported_users[reported_id]['reasons'].append("Test report oleh admin")
    save_reports()

    await put_in_prison(reported_user)
    await ctx.send(f"✅ **{reported_user.mention} langsung mendapatkan 15 report dan masuk penjara!**")


@bot.command()
async def cancelprisoner(ctx, member: discord.Member):
    if ctx.author.id not in ADMIN_USER_IDS and ctx.author.id != ctx.guild.owner_id:
        await ctx.send("❌ **Anda tidak memiliki izin untuk membebaskan tahanan!**")
        return

    prison_role = discord.utils.get(ctx.guild.roles, name=PRISON_ROLE_NAME)

    if (not prison_role or prison_role not in member.roles) and member.id not in user_roles_before_prison:
        await ctx.send("❌ **User ini tidak sedang di penjara!**")
        return

    await release_from_prison(member)
    await ctx.send(f"✅ **{member.mention} dibebaskan dari penjara!**")


@bot.command()
async def cek(ctx, user: discord.Member):
    count = reported_users.get(str(user.id), {'count': 0})['count']

    # Periksa apakah user memiliki role "Prisoner"
    prisoner_role = discord.utils.get(user.roles, name="Prisoner")
    prison_status = "✅ Sedang dalam penjara" if prisoner_role else "❌ Tidak dalam penjara"

    # Periksa apakah pengguna saat ini memiliki izin DM ke user ini
    has_dm_permission = str(ctx.author.id) in dm_permissions.get(str(user.id), [])
    dm_status = "✅ Anda memiliki izin mengirim DM" if has_dm_permission else "❌ Anda tidak memiliki izin mengirim DM"

    await ctx.send(f"ℹ️ **Info {user.mention}:**\n📊 Jumlah laporan: {count}\n🔒 Status: {prison_status}\n📨 Status DM: {dm_status}")


@bot.command()
async def ping(ctx):
    latency = round(bot.latency * 1000)
    await ctx.send(f'🏓 Pong! Latency: {latency}ms')


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ **Perintah tidak lengkap! Gunakan `!help {ctx.command}` untuk melihat cara penggunaan.**")
    elif isinstance(error, commands.CommandNotFound):
        pass  # Abaikan perintah yang tidak ditemukan
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ **User tidak ditemukan!**")
    else:
        await ctx.send(f"❌ **Terjadi kesalahan: {str(error)}**")
        print(f"Error dalam perintah {ctx.command}: {error}")


@bot.command()
async def cleanup_reports(ctx, user: discord.Member = None):
    # Only allow admins to run this
    if ctx.author.id not in ADMIN_USER_IDS:
        await ctx.send("❌ **Anda tidak memiliki izin untuk menggunakan perintah ini!**")
        return
    
    # If a specific user is provided
    if user:
        user_id = str(user.id)
        if user_id in reported_users:
            old_count = reported_users[user_id]['count']
            reported_users[user_id]['count'] = 0
            save_reports()
            await ctx.send(f"✅ **Berhasil reset count untuk {user.mention} dari {old_count} menjadi 0!**")
            await log_activity(f"🧹 **{ctx.author.mention} melakukan cleanup reports untuk {user.mention}.**")
        else:
            await ctx.send(f"ℹ️ **{user.mention} tidak memiliki laporan apapun.**")
        return
    
    # If no specific user, clean all users with count >= 15
    count_fixed = 0
    
    for user_id, data in reported_users.items():
        if data['count'] >= 15:
            data['count'] = 0
            count_fixed += 1
    
    save_reports()
    
    await ctx.send(f"✅ **Berhasil reset count untuk {count_fixed} user yang memiliki laporan >= 15!**")
    await log_activity(f"🧹 **{ctx.author.mention} melakukan cleanup reports. {count_fixed} user direset.**")
    
@bot.command(name="helpme")
async def help_command(ctx, command_name: str = None):  # Tambahkan parameter opsional
    if command_name:
        # Bantuan untuk perintah tertentu
        command = bot.get_command(command_name)
        if not command:
            await ctx.send(f"❌ **Perintah `{command_name}` tidak ditemukan!**")
            return

        help_text = f"**Cara penggunaan `!{command.name}`:**\n"

        command_help = {
            "openreport": "```!openreport @user alasan```\nMelaporkan user dengan alasan tertentu.",
            "dm": "```!dm user_id pesan```\nMengirim pesan langsung (DM) ke user tertentu. **Catatan:** Hanya dapat digunakan oleh reporter ketiga.",
            "voterelease": "```!voterelease @user```\nMemulai voting untuk membebaskan user dari penjara.",
            "setuju": "```!setuju [@user]```\nMemberikan suara untuk membebaskan user dari penjara.",
            "testreport": "```!testreport @user```\nAdmin only: Langsung memberi 15 report dan memenjarakan user.",
            "cancelprisoner": "```!cancelprisoner @user```\nMembebaskan user dari penjara.",
            "cek": "```!cek @user```\nMelihat jumlah laporan pada user tertentu dan status izin DM.",
            "ping": "```!ping```\nMengecek latensi bot.",
        }

        help_text += command_help.get(command.name, "Belum ada dokumentasi untuk perintah ini.")

        await ctx.send(help_text)
    else:
        # Daftar semua perintah
        help_text = "**📋 Daftar Perintah:**\n"
        help_text += "• `!openreport @user alasan` - Melaporkan user\n"
        help_text += "• `!dm user_id pesan` - Mengirim DM ke user (khusus reporter ketiga)\n"
        help_text += "• `!voterelease @user` - Voting bebaskan dari penjara\n"
        help_text += "• `!setuju [@user]` - Memberikan suara untuk membebaskan\n"
        help_text += "• `!cek @user` - Melihat jumlah laporan user\n"
        help_text += "• `!ping` - Cek latensi bot\n"
        help_text += "• `!helpme [perintah]` - Menampilkan bantuan\n\n"
        help_text += "Gunakan `!helpme [perintah]` untuk informasi detail tentang perintah tertentu."

        await ctx.send(help_text)


# Jalankan bot
if __name__ == "__main__":
    # Pastikan file diperlukan ada
    ensure_directory_exists()
    load_dotenv()
    TOKEN = os.getenv("TOKEN_BOT")
    if not TOKEN:
        print("❌ ERROR: Token bot tidak ditemukan.")
        sys.exit(1)

    bot.run(TOKEN, reconnect=True)