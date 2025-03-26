import discord
from discord.ext import commands
import time
import logging
import asyncio
from shared import (
    is_mod_or_admin,
    log_activity,
    save_data,
    PRISON_ROLE_NAME,
    reported_users,
    user_roles_before_prison,
    user_nicknames_before_prison,
    PRISON_DATA_FILE,
    REPORT_DATA_FILE,
    PRISON_DURATION,
    dm_permissions
)

logger = logging.getLogger("discord_bot")

class AdminCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def save_reports(self):
        """Save reports data to file"""
        save_data(reported_users, REPORT_DATA_FILE, default_factory=lambda: {
            'count': 0,
            'reasons': [],
            'last_report': 0
        })

    async def save_prison_state(self):
        """Save prison state to file"""
        data = {
            'user_roles': {
                str(user_id): [role.id for role in roles]  # Convert to string for JSON
                for user_id, roles in user_roles_before_prison.items()
            },
            'user_nicknames': dict(user_nicknames_before_prison)
        }
        save_data(data, PRISON_DATA_FILE)

    async def put_in_prison(self, member):
        """Put a member in prison"""
        if not member:
            return False
        
        if member.id == member.guild.owner_id:
            return False
        
        try:
            guild = member.guild
            prison_role = discord.utils.get(guild.roles, name=PRISON_ROLE_NAME)
            
            if not prison_role:
                try:
                    prison_role = await guild.create_role(
                        name=PRISON_ROLE_NAME,
                        permissions=discord.Permissions.none()
                    )
                    
                    for channel in guild.channels:
                        await channel.set_permissions(prison_role, 
                                                   send_messages=False,
                                                   add_reactions=False)
                except discord.errors.Forbidden:
                    await log_activity(self.bot, f"❌ Failed to create prison role - insufficient permissions")
                    return False
            
            # Store current roles and nickname
            user_roles_before_prison[member.id] = [role for role in member.roles if not role.is_default()]
            user_nicknames_before_prison[str(member.id)] = member.display_name
            
            try:
                # Set prisoner nickname
                new_nick = f"🔒 Prisoner"
                if len(new_nick) > 32:
                    new_nick = new_nick[:32]
                
                # Apply prison role and remove all other roles
                await member.edit(
                    roles=[prison_role],
                    nick=new_nick
                )
                
                await log_activity(self.bot, f"🔒 {member.mention} has been imprisoned for 1 hour!")
                
                await self.save_prison_state()
                asyncio.create_task(self.release_after_delay(member, PRISON_DURATION))
                return True
            except discord.errors.Forbidden:
                await log_activity(self.bot, f"❌ Failed to imprison {member.mention} - insufficient permissions")
                return False
        except Exception as e:
            await log_activity(self.bot, f"❌ Error imprisoning {member.mention}: {e}")
            logger.error(f"Error in put_in_prison: {e}")
            return False

    async def release_from_prison(self, member):
        """Release a member from prison"""
        if not member:
            return False
        
        try:
            prison_role = discord.utils.get(member.guild.roles, name=PRISON_ROLE_NAME)
            is_in_prison_by_role = prison_role and prison_role in member.roles
            is_in_prison_by_tracking = member.id in user_roles_before_prison or str(member.id) in user_nicknames_before_prison
            
            if not is_in_prison_by_role and not is_in_prison_by_tracking:
                return False
                
            # Get original roles
            original_roles = []
            if member.id in user_roles_before_prison:
                # Convert role IDs to role objects if needed
                if isinstance(user_roles_before_prison[member.id][0], int):
                    original_roles = [
                        member.guild.get_role(role_id) 
                        for role_id in user_roles_before_prison[member.id]
                        if member.guild.get_role(role_id) is not None
                    ]
                else:
                    original_roles = [
                        role for role in user_roles_before_prison[member.id] 
                        if hasattr(role, 'is_default') and not role.is_default()
                    ]
            
            # Remove prison role first
            if prison_role and prison_role in member.roles:
                await member.remove_roles(prison_role)
            
            # Restore original roles
            if original_roles:
                roles_to_restore = [
                    role for role in original_roles 
                    if role and role != prison_role
                ]
                if roles_to_restore:
                    await member.add_roles(*roles_to_restore)
            
            # Restore nickname
            member_id_str = str(member.id)
            original_nick = user_nicknames_before_prison.pop(member_id_str, None)
            
            if original_nick:
                try:
                    await member.edit(nick=original_nick)
                    await log_activity(self.bot, f"🔓 Restored original nickname '{original_nick}' to {member.mention}")
                except discord.errors.Forbidden:
                    await log_activity(self.bot, f"⚠️ Failed to restore nickname for {member.mention} due to permissions")
            else:
                current_nick = member.display_name
                if current_nick.startswith("🔒 Prisoner"):
                    try:
                        await member.edit(nick=None)
                        await log_activity(self.bot, f"🔓 Reset nickname for {member.mention} to default username")
                    except discord.errors.Forbidden:
                        await log_activity(self.bot, f"⚠️ Failed to reset nickname for {member.mention} due to permissions")
            
            # Clean up tracking
            if member.id in user_roles_before_prison:
                del user_roles_before_prison[member.id]
            
            await self.save_prison_state()
            await log_activity(self.bot, f"🔓 {member.mention} has been released from prison!")
            return True
            
        except discord.errors.Forbidden:
            await log_activity(self.bot, f"❌ Failed to release {member.mention} - insufficient permissions")
            return False
        except Exception as e:
            await log_activity(self.bot, f"❌ Error releasing {member.mention}: {e}")
            logger.error(f"Error in release_from_prison: {e}")
            return False

    async def release_after_delay(self, member, delay):
        """Release member after a delay"""
        await asyncio.sleep(delay)
        await self.release_from_prison(member)

    @commands.command()
    @commands.check(is_mod_or_admin)
    async def testreport(self, ctx, reported_user: discord.Member):
        """Test report function (Admin only)"""
        if reported_user.id == ctx.guild.owner_id:
            for _ in range(10):
                await reported_user.send("NO NO YA DEK!")
            await ctx.send(f"⚠️ **{reported_user.mention} has received 15 reports but is the owner and cannot be imprisoned. Sent 10 'NO NO YA DEK' messages to their DM.**")
            return

        reported_id = str(reported_user.id)
        reported_users[reported_id] = {
            'count': 15,
            'reasons': ["Test report oleh admin"],
            'last_report': time.time()
        }
        await self.save_reports()
        await self.put_in_prison(reported_user)
        await ctx.send(f"✅ **{reported_user.mention} langsung mendapatkan 15 report dan masuk penjara!**")

    @commands.command()
    @commands.check(is_mod_or_admin)
    async def cancelprisoner(self, ctx, member: discord.Member = None):
        """Membebaskan user dari penjara (Admin only)"""
        if not member:
            await ctx.send("❌ **Gunakan format yang benar:** `!cancelprisoner @user`")
            return
        
        prison_role = discord.utils.get(ctx.guild.roles, name=PRISON_ROLE_NAME)
        if not prison_role or prison_role not in member.roles:
            await ctx.send(f"❌ **{member.mention} tidak sedang dalam penjara!**")
            return
        
        try:
            # Release from prison
            success = await self.release_from_prison(member)
            if not success:
                await ctx.send("❌ Failed to release prisoner!")
                return
            
            # Reset reports
            if str(member.id) in reported_users:
                reported_users[str(member.id)]['count'] = 0
                save_data(reported_users, REPORT_DATA_FILE)
            
            await ctx.send(f"🔓 **{member.mention} telah dibebaskan dari penjara oleh {ctx.author.mention}!**")
            await log_activity(self.bot, f"🔓 **{ctx.author.mention} membebaskan {member.mention} dari penjara**")
            
        except Exception as e:
            logger.error(f"Error in cancelprisoner: {e}")
            await ctx.send(f"❌ **Terjadi error saat membebaskan {member.mention}: {str(e)}**")

    @commands.command()
    @commands.check(is_mod_or_admin)
    async def resetreports(self, ctx, member: discord.Member = None):
        """Reset reports for a user or all users"""
        if member:
            reported_users.pop(str(member.id), None)
            await ctx.send(f"✅ Reports for {member.mention} have been reset!")
        else:
            reported_users.clear()
            await ctx.send("✅ All reports have been reset!")
        
        save_data(reported_users, REPORT_DATA_FILE, default_factory=lambda: {
            'count': 0,
            'reasons': [],
            'last_report': 0
        })

    @commands.command()
    @commands.check(is_mod_or_admin)
    async def cleanup_reports(self, ctx, member: discord.Member = None):
        """Cleanup reports for users with >= 15 reports"""
        cleaned = 0
        
        if member:
            if str(member.id) in reported_users and reported_users[str(member.id)]['count'] >= 15:
                reported_users.pop(str(member.id))
                cleaned += 1
                await ctx.send(f"✅ Cleaned reports for {member.mention}")
            else:
                await ctx.send(f"❌ {member.mention} doesn't have enough reports to clean")
        else:
            to_remove = [uid for uid, data in reported_users.items() if data['count'] >= 15]
            cleaned = len(to_remove)
            for uid in to_remove:
                reported_users.pop(uid)
            await ctx.send(f"✅ Cleaned {cleaned} users with excessive reports")
        
        if cleaned > 0:
            save_data(reported_users, REPORT_DATA_FILE, default_factory=lambda: {
                'count': 0,
                'reasons': [],
                'last_report': 0
            })

async def setup(bot):
    await bot.add_cog(PointSystem(bot, ADMIN_USER_IDS)) 