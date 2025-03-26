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
from shared import (
    load_data,
    save_data,
    PRISON_DATA_FILE,
    REPORT_DATA_FILE,
    PRISON_ROLE_NAME,
    user_roles_before_prison,
    user_nicknames_before_prison,
    imprisonment_times,
    reported_users,
    log_activity
)

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
intents.voice_states = True

# Initialize bot
bot = commands.Bot(command_prefix='!', intents=intents)

# Constants
LOG_CHANNEL_IDS = [1351561404150448248, 1350543441821564988]
PRISON_DURATION = 3600  # 1 hour
VOTE_DURATION = 300  # 5 minutes
VOTE_COOLDOWN = 900  # 15 minutes
ADMIN_USER_IDS = [776744923738800129]
MOD_ROLE_NAME = "Moderator"
REQUIRED_VOTES = 3
REPORT_COOLDOWN = 3600  # 1 hour cooldown between reports

# Define SCRIPT_DIR at the top of your script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def ensure_directory_exists():
    """Ensure the data directory exists"""
    os.makedirs(SCRIPT_DIR, exist_ok=True)

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
                    permissions=discord.Permissions.none(),
                    reason="Prison system initialization"
                )
                
                for channel in guild.channels:
                    try:
                        await channel.set_permissions(
                            prison_role,
                            send_messages=False,
                            add_reactions=False,
                            connect=False,
                            speak=False,
                            view_channel=True
                        )
                    except Exception as e:
                        logger.debug(f"Couldn't set permissions for {channel.name}: {e}")
                        continue
            except Exception as e:
                logger.error(f"Failed to create prison role in {guild.name}: {e}")
                continue

        # Restore prisoners
        if 'user_roles' in prison_data:
            for user_id_str, role_ids in prison_data['user_roles'].items():
                try:
                    user_id = int(user_id_str)
                    member = guild.get_member(user_id)
                    if not member:
                        continue

                    # Calculate remaining prison time
                    imprisonment_time = prison_data.get('imprisonment_times', {}).get(user_id_str, 0)
                    remaining_time = max(0, (imprisonment_time + PRISON_DURATION) - time.time())

                    if remaining_time > 0:
                        # Store original data
                        user_roles_before_prison[user_id] = []
                        for role_id in role_ids:
                            role = guild.get_role(int(role_id))
                            if role:
                                user_roles_before_prison[user_id].append(role)
                        
                        if 'user_nicknames' in prison_data:
                            user_nicknames_before_prison[user_id_str] = prison_data['user_nicknames'].get(user_id_str, "")

                        # Apply prison state
                        if prison_role not in member.roles:
                            await member.add_roles(prison_role)
                        
                        # Set prisoner nickname
                        new_nick = f"🔒 Prisoner"
                        if len(new_nick) > 32:
                            new_nick = new_nick[:32]
                        try:
                            await member.edit(nick=new_nick)
                        except discord.errors.Forbidden:
                            pass
                        
                        # Schedule release
                        asyncio.create_task(release_after_delay(member, remaining_time))
                        
                except Exception as e:
                    logger.error(f"Error restoring prisoner {user_id_str}: {e}")
                    continue

@tasks.loop(minutes=5)
async def check_prison_releases():
    """Background task to check and release prisoners with auto-fix"""
    for guild in bot.guilds:
        try:
            prison_role = discord.utils.get(guild.roles, name=PRISON_ROLE_NAME)
            if not prison_role:
                continue

            for member in guild.members:
                try:
                    if prison_role in member.roles:
                        if member.id not in user_roles_before_prison:
                            logger.info(f"Auto-adding {member.name} to prison system")

                            user_roles_before_prison[member.id] = [
                                role for role in member.roles 
                                if role != prison_role and not role.is_default()
                            ]
                            user_nicknames_before_prison[str(member.id)] = member.display_name
                            imprisonment_times[member.id] = time.time()
                            
                            try:
                                new_nick = f"🔒 Prisoner"
                                if len(new_nick) > 32:
                                    new_nick = new_nick[:32]
                                await member.edit(nick=new_nick)
                            except discord.errors.Forbidden:
                                pass
                            
                            save_data({
                                'user_roles': {
                                    str(user_id): [role.id for role in roles]
                                    for user_id, roles in user_roles_before_prison.items()
                                },
                                'user_nicknames': dict(user_nicknames_before_prison),
                                'imprisonment_times': imprisonment_times
                            }, PRISON_DATA_FILE)
                            
                            asyncio.create_task(release_after_delay(member, PRISON_DURATION))
                            
                except Exception as e:
                    logger.error(f"Error processing prisoner {member.name}: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error checking prison in {guild.name}: {e}")
            continue

async def release_after_delay(member, delay):
    """Release member after a delay"""
    await asyncio.sleep(delay)
    await release_from_prison(member)

async def release_from_prison(member):
    """Release a member from prison"""
    if not member:
        return False
    
    try:
        prison_role = discord.utils.get(member.guild.roles, name=PRISON_ROLE_NAME)
        if not prison_role or prison_role not in member.roles:
            return False
            
        # Release from prison
        original_roles = user_roles_before_prison.pop(member.id, [])
        
        if not original_roles:
            await member.remove_roles(prison_role)
        else:
            roles_to_apply = [role for role in original_roles if role.id != prison_role.id]
            await member.edit(roles=roles_to_apply)
        
        # Reset nickname
        original_nick = user_nicknames_before_prison.pop(str(member.id), None)
        if original_nick:
            try:
                await member.edit(nick=original_nick)
            except discord.errors.Forbidden:
                pass
        elif member.display_name.startswith("🔒 Prisoner"):
            try:
                await member.edit(nick=None)
            except discord.errors.Forbidden:
                pass
        
        # Reset reports
        if str(member.id) in reported_users:
            reported_users[str(member.id)]['count'] = 0
            save_data(reported_users, REPORT_DATA_FILE)
        
        # Save prison state
        save_data({
            'user_roles': {
                str(user_id): [role.id for role in roles]
                for user_id, roles in user_roles_before_prison.items()
            },
            'user_nicknames': dict(user_nicknames_before_prison),
            'imprisonment_times': imprisonment_times
        }, PRISON_DATA_FILE)
        
        await log_activity(bot, f"🔓 {member.mention} has been released from prison!")
        return True
        
    except Exception as e:
        await log_activity(bot, f"❌ Error releasing {member.mention}: {e}")
        logger.error(f"Error in release_from_prison: {e}")
        return False

@bot.event
async def on_ready():
    """Bot initialization when ready"""
    print(f'Bot {bot.user} is now online!')
    await log_activity(bot, f'✅ **Bot {bot.user} is back online after restart!**')

    # Load and restore prison state for all guilds
    prison_data = load_data(PRISON_DATA_FILE)
    await restore_prison_state(prison_data)
    
    check_prison_releases.start()
    await setup(bot)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ **Perintah tidak lengkap! Gunakan `!help {ctx.command}` untuk melihat cara penggunaan.**")
    elif isinstance(error, commands.CommandNotFound):
        pass
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ **User tidak ditemukan!**")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ **Anda tidak memiliki izin untuk menggunakan perintah ini!**")
    else:
        logger.error(f"Command error: {error}")
        await ctx.send(f"❌ **Terjadi kesalahan: {str(error)}**")

async def setup(bot):
    """Setup function for cogs"""
    from admin_commands import AdminCommands
    from user_commands import UserCommands
    from point_system import PointSystem
    
    await bot.add_cog(AdminCommands(bot))
    await bot.add_cog(UserCommands(bot))
    await bot.add_cog(PointSystem(bot))

if __name__ == "__main__":
    ensure_directory_exists()
    load_dotenv()
    TOKEN = os.getenv("TOKEN_BOT")
    if not TOKEN:
        print("❌ ERROR: Token bot tidak ditemukan.")
        sys.exit(1)

    bot.run(TOKEN, reconnect=True)