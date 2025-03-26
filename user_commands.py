import discord
from discord.ui import Modal, TextInput, Select, View
from discord.ext import commands
import time
from collections import defaultdict
import asyncio
from shared import (
    PRISON_ROLE_NAME,
    PRISON_DURATION,
    PRISON_DATA_FILE,
    log_activity,
    user_roles_before_prison,
    user_nicknames_before_prison,
    imprisonment_times,
    save_data,
    REPORT_DATA_FILE,
    REPORT_NOTICE_THRESHOLD,
    REPORT_DM_THRESHOLD,
    REPORT_PRISON_THRESHOLD,
    REPORT_COOLDOWN,
    reported_users,
    dm_permissions
)

# Voting Constants
VOTE_RELEASE_THRESHOLD = 3  # Minimal 3 votes
VOTE_RELEASE_DURATION = 300  # 5 minutes voting time (300 seconds)
VOTE_RELEASE_COOLDOWN = 600  # 10 minutes cooldown (600 seconds)

class VoteReleaseView(View):
    def __init__(self, target_user: discord.Member):
        super().__init__(timeout=VOTE_RELEASE_DURATION)
        self.target_user = target_user
        self.votes = set()  # Stores voter IDs
        self.message = None
        self.success = False

    async def on_timeout(self):
        if not self.success and self.message:
            try:
                await self.message.delete()
            except:
                pass

    @discord.ui.button(label="Vote Bebaskan", style=discord.ButtonStyle.green, emoji="🔓")
    async def vote_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.votes:
            await interaction.response.send_message("⚠️ Anda sudah vote!", ephemeral=True, delete_after=3)
            return

        self.votes.add(interaction.user.id)
        
        # Update vote count
        await interaction.response.edit_message(
            content=self._build_message_content(),
            view=self
        )

        if len(self.votes) >= VOTE_RELEASE_THRESHOLD:
            await self._handle_success(interaction)

    def _build_message_content(self):
        return (
            f"**🗳️ VOTE BEBASKAN**\n"
            f"🔒 Tahanan: {self.target_user.mention}\n"
            f"✅ {len(self.votes)}/{VOTE_RELEASE_THRESHOLD} vote\n"
            f"⏳ Sisa waktu: {int((self.timeout or 0)/60)} menit"
        )

    async def _handle_success(self, interaction: discord.Interaction):
        self.success = True
        cog = interaction.client.get_cog("UserCommands")
        
        if cog and await cog.release_from_prison(self.target_user):
            msg = await interaction.followup.send(
                f"🎉 {self.target_user.mention} berhasil dibebaskan!"
            )
            # Manually delete after delay since followup.send doesn't support delete_after
            asyncio.create_task(self._delete_after(msg, 30))
        else:
            msg = await interaction.followup.send(
                f"❌ Gagal membebaskan {self.target_user.mention}"
            )
            asyncio.create_task(self._delete_after(msg, 30))
        
        # Cleanup
        if self.message:
            await asyncio.sleep(5)
            try:
                await self.message.delete()
            except:
                pass
        self.stop()

    async def _delete_after(self, message, delay):
        """Helper to delete a message after a delay"""
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except:
            pass

class ReportDMForm(Modal):
    def __init__(self, target_user: discord.Member, original_message: discord.Message):
        super().__init__(title=f"DM Warning to {target_user.display_name}", timeout=180)
        self.target_user = target_user
        self.original_message = original_message
        self.message_input = TextInput(
            label="Your warning message",
            style=discord.TextStyle.long,
            placeholder="Type your warning message here...",
            required=True
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await self.target_user.send(
                f"⚠️ Warning from {interaction.user.mention}:\n"
                f"{self.message_input.value}"
            )
            # Delete the original message containing the DM button
            try:
                await self.original_message.delete()
            except:
                pass
            
            # Show success notification that will auto-delete
            await interaction.response.send_message(
                "✅ Your warning has been sent!",
                ephemeral=True,
                delete_after=5
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Failed to send DM: {str(e)}",
                ephemeral=True,
                delete_after=10
            )

class DMButtonView(discord.ui.View):
    def __init__(self, target_user: discord.Member, original_message: discord.Message):
        super().__init__(timeout=180)
        self.target_user = target_user
        self.original_message = original_message
        self.has_interacted = False

    async def on_timeout(self):
        # Automatically delete the DM offer message when timeout occurs
        try:
            await self.original_message.delete()
        except:
            pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Prevent multiple interactions
        if self.has_interacted:
            await interaction.response.send_message(
                "❌ You've already used this DM button",
                ephemeral=True,
                delete_after=5
            )
            return False
        return True

    @discord.ui.button(label="Send DM", style=discord.ButtonStyle.primary)
    async def send_dm(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Mark as interacted and disable the button
        self.has_interacted = True
        button.disabled = True
        button.label = "DM Sent"
        button.style = discord.ButtonStyle.secondary
        
        # Update the message to show disabled button
        await interaction.message.edit(view=self)
        
        # Send the modal form
        await interaction.response.send_modal(ReportDMForm(self.target_user, self.original_message))

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        # Handle any errors that occur
        await interaction.response.send_message(
            "❌ An error occurred while processing your request",
            ephemeral=True,
            delete_after=10
        )

class UserCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.remove_command('help')  # Remove default help command
        self.active_votes = {}
        self.last_vote_time = defaultdict(int)
        self.user_reports = defaultdict(list)
        self.report_cooldowns = {}
        self.release_votes = {}

    async def put_in_prison(self, member):
        """Put a member in prison"""
        if not member:
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
            
            # Store current roles as IDs and nickname
            user_roles_before_prison[member.id] = [role.id for role in member.roles if not role.is_default()]
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
                
                save_data({
                    'user_roles': user_roles_before_prison,
                    'user_nicknames': user_nicknames_before_prison,
                    'imprisonment_times': imprisonment_times
                }, PRISON_DATA_FILE)
                
                asyncio.create_task(self.release_after_delay(member, PRISON_DURATION))
                return True
            except discord.errors.Forbidden:
                await log_activity(self.bot, f"❌ Failed to imprison {member.mention} - insufficient permissions")
                return False
        except Exception as e:
            await log_activity(self.bot, f"❌ Error imprisoning {member.mention}: {e}")
            return False

    async def release_after_delay(self, member, delay):
        """Release member after a delay"""
        await asyncio.sleep(delay)
        await self.release_from_prison(member)

    async def release_from_prison(self, member):
        """Release a member from prison with proper role and nickname restoration"""
        if not member:
            return False
        
        try:
            guild = member.guild
            prison_role = discord.utils.get(guild.roles, name=PRISON_ROLE_NAME)
            if not prison_role or prison_role not in member.roles:
                return False
                
            # Get original data
            original_roles_ids = user_roles_before_prison.get(member.id, [])
            original_nick = user_nicknames_before_prison.get(str(member.id))
            
            # Convert role IDs back to role objects
            original_roles = []
            for role_id in original_roles_ids:
                role = guild.get_role(role_id)
                if role:
                    original_roles.append(role)
            
            # Remove prison role first
            await member.remove_roles(prison_role)
            
            # Restore original roles (filter out None and prison role)
            if original_roles:
                roles_to_add = [
                    role for role in original_roles 
                    if role is not None and role != prison_role
                ]
                if roles_to_add:
                    try:
                        await member.add_roles(*roles_to_add)
                    except discord.Forbidden:
                        await log_activity(self.bot, f"❌ Tidak bisa mengembalikan role untuk {member.mention}")
            
            # Restore nickname
            try:
                if original_nick is not None:
                    await member.edit(nick=original_nick)
                else:
                    # If no original nick, remove prisoner nick if exists
                    if member.display_name.startswith("🔒"):
                        await member.edit(nick=None)
            except discord.Forbidden:
                await log_activity(self.bot, f"❌ Tidak bisa mengembalikan nickname untuk {member.mention}")
            
            # Clean up stored data
            if member.id in user_roles_before_prison:
                del user_roles_before_prison[member.id]
            if str(member.id) in user_nicknames_before_prison:
                del user_nicknames_before_prison[str(member.id)]
            
            # Save prison state
            save_data({
                'user_roles': user_roles_before_prison,
                'user_nicknames': user_nicknames_before_prison,
                'imprisonment_times': imprisonment_times
            }, PRISON_DATA_FILE)
            
            await log_activity(self.bot, f"🔓 {member.mention} telah dibebaskan dari penjara!")
            return True
            
        except Exception as e:
            await log_activity(self.bot, f"❌ Error saat membebaskan {member.mention}: {str(e)}")
            return False

    @commands.command(aliases=['votebebas'])
    async def voterelease(self, ctx, member: discord.Member):
        """Mulai voting pembebasan (3 vote dalam 5 menit)"""
        prison_role = discord.utils.get(ctx.guild.roles, name=PRISON_ROLE_NAME)
        
        # Verify prisoner status
        if not prison_role or prison_role not in member.roles:
            await ctx.send(f"{member.mention} tidak dipenjara!", delete_after=10)
            return
        
        # Check cooldown
        current_time = time.time()
        if member.id in self.release_votes:
            remaining = self.release_votes[member.id]['end_time'] - current_time
            if remaining > 0:
                await ctx.send(
                    f"⏳ Tunggu {int(remaining/60)} menit untuk vote lagi!",
                    delete_after=10
                )
                return
        
        # Start new vote
        view = VoteReleaseView(member)
        message = await ctx.send(view._build_message_content(), view=view)
        view.message = message
        
        # Store vote data
        self.release_votes[member.id] = {
            'end_time': current_time + VOTE_RELEASE_COOLDOWN,
            'view': view
        }
        
        # Auto cleanup after timeout
        await asyncio.sleep(VOTE_RELEASE_DURATION)
        if member.id in self.release_votes and not view.success:
            del self.release_votes[member.id]

    @commands.command(aliases=['openreport'])
    async def report(self, ctx, member: discord.Member, *, reason: str = None):
        """Report a user to moderators"""
        current_time = time.time()
        
        # Cooldown for reporting the same user
        cooldown_key = (ctx.author.id, member.id)
        if cooldown_key in self.report_cooldowns:
            remaining = self.report_cooldowns[cooldown_key] - current_time
            if remaining > 0:
                await ctx.send(
                    f"⏳ You can report {member.mention} again in {int(remaining/60)} minutes! "
                    f"(Cooldown applies per user)",
                    ephemeral=True,
                    delete_after=10
                )
                return

        formatted_reason = reason if reason else "No reason provided"
        full_reason = f"{formatted_reason} (Reported by: {ctx.author.name})"
        
        if str(member.id) not in reported_users:
            reported_users[str(member.id)] = {
                'count': 0,
                'reasons': [],
                'last_report': 0
            }

        reported_users[str(member.id)]['count'] += 1
        reported_users[str(member.id)]['reasons'].append(full_reason)
        reported_users[str(member.id)]['last_report'] = current_time
        self.report_cooldowns[cooldown_key] = current_time + REPORT_COOLDOWN

        save_data(reported_users, REPORT_DATA_FILE)

        await log_activity(self.bot, 
            f"⚠️ **New Report**\n"
            f"• Target: {member.mention}\n"
            f"• Reporter: {ctx.author.mention}\n"
            f"• Reason: {formatted_reason}\n"
            f"• Total Reports: {reported_users[str(member.id)]['count']}/{REPORT_PRISON_THRESHOLD}"
        )

        report_count = reported_users[str(member.id)]['count']
        
        if report_count == REPORT_NOTICE_THRESHOLD:
            notice_msg = await ctx.send(f"⚠️ WARNING {member.mention} has received {REPORT_NOTICE_THRESHOLD} reports!")
            await asyncio.sleep(30)
            try:
                await notice_msg.delete()
            except:
                pass
        
        elif report_count == REPORT_DM_THRESHOLD:
            if member.id not in dm_permissions:
                dm_permissions[member.id] = []
            if ctx.author.id not in dm_permissions[member.id]:
                dm_permissions[member.id].append(ctx.author.id)
            
            # Send DM offer and store the message reference
            dm_message = await ctx.send(
                f"⚠️ {ctx.author.mention}, you can send a warning DM to {member.mention}",
                view=DMButtonView(member, ctx.message)
            )
        
        elif report_count >= REPORT_PRISON_THRESHOLD:
            if await self.put_in_prison(member):
                reported_users[str(member.id)]['count'] = 0
                save_data(reported_users, REPORT_DATA_FILE)
                prison_msg = await ctx.send(
                    f"🔒 {member.mention} has been IMPRISONED!\n"
                    f"Reason: Too many reports ({REPORT_PRISON_THRESHOLD}+)"
                )
                await asyncio.sleep(60)
                try:
                    await prison_msg.delete()
                except:
                    pass
            else:
                fail_msg = await ctx.send(f"❌ Failed to imprison {member.mention}")
                await asyncio.sleep(10)
                try:
                    await fail_msg.delete()
                except:
                    pass

        else:
            report_msg = await ctx.send(
                f"✅ Report against {member.mention} recorded!\n"
                f"• Reason: {formatted_reason}\n"
                f"• Total Reports: {report_count}/{REPORT_PRISON_THRESHOLD}",
                ephemeral=True,
                delete_after=15
            )

    @commands.command(aliases=['cek'])
    async def check_reports(self, ctx, member: discord.Member = None):
        """Check report count and recent reasons for a user"""
        if member:
            user_data = reported_users.get(str(member.id), {})
            count = user_data.get('count', 0)
            reasons = user_data.get('reasons', [])
            
            embed = discord.Embed(
                title=f"📊 Reports for {member.display_name}",
                description=f"Total reports: {count}",
                color=discord.Color.orange()
            )
            
            if reasons:
                latest_reasons = reasons[-5:][::-1]
                reasons_text = "\n\n".join(
                    f"**{i+1}.** {reason}" 
                    for i, reason in enumerate(latest_reasons)
                )
                embed.add_field(name="Latest 5 Reasons", value=reasons_text, inline=False)
            
            report_msg = await ctx.send(embed=embed)
            await asyncio.sleep(120)
            try:
                await report_msg.delete()
            except:
                pass
        else:
            embed = discord.Embed(
                title="📊 Report Summary",
                description="Users with active reports",
                color=discord.Color.orange()
            )
            
            for user_id, data in reported_users.items():
                if data.get('count', 0) > 0:
                    user = ctx.guild.get_member(int(user_id))
                    if user:
                        embed.add_field(
                            name=user.display_name,
                            value=f"{data['count']} reports",
                            inline=True
                        )
            
            summary_msg = await ctx.send(embed=embed)
            await asyncio.sleep(120)
            try:
                await summary_msg.delete()
            except:
                pass

    @commands.command()
    async def ping(self, ctx):
        """Check bot latency"""
        latency = round(self.bot.latency * 1000)
        ping_msg = await ctx.send(f"🏓 Pong! Latency: {latency}ms", delete_after=10)

    @commands.command(name='helpme')
    async def custom_help(self, ctx):
        """Show interactive help menu"""
        view = discord.ui.View(timeout=60)
        
        select = Select(
            placeholder="Select command category...",
            options=[
                discord.SelectOption(
                    label="👤 User Commands",
                    description="Commands for all members",
                    value="user",
                    emoji="👤"
                ),
                discord.SelectOption(
                    label="🛠️ Admin Commands",
                    description="Moderator only commands",
                    value="admin",
                    emoji="🛠️"
                ),
                discord.SelectOption(
                    label="⚖️ Report System",
                    description="About the report system",
                    value="report",
                    emoji="⚖️"
                ),
                discord.SelectOption(
                    label="🔓 Prison System",
                    description="About the prison system",
                    value="prison",
                    emoji="🔓"
                )
            ]
        )
        
        async def select_callback(interaction: discord.Interaction):
            value = select.values[0]
            embed = discord.Embed(color=discord.Color.blue())
            
            if value == "user":
                embed.title = "👤 USER COMMANDS"
                commands_list = [
                    ("!openreport @user [reason]", "Report a user to moderators"),
                    ("!cek [@user]", "Check a user's report history"),
                    ("!ping", "Check bot latency"),
                    ("!helpme", "Show this help menu")
                ]
            elif value == "admin":
                embed.title = "🛠️ ADMIN COMMANDS"
                commands_list = [
                    ("!testreport @user", "Immediately jail a user (15 reports)"),
                    ("!cancelprisoner @user", "Release a user from prison"),
                    ("!resetreports [@user]", "Reset a user's report count")
                ]
            elif value == "report":
                embed.title = "⚖️ REPORT SYSTEM"
                commands_list = [
                    ("Warning Threshold", f"{REPORT_NOTICE_THRESHOLD} reports"),
                    ("DM Threshold", f"{REPORT_DM_THRESHOLD} reports"),
                    ("Prison Threshold", f"{REPORT_PRISON_THRESHOLD} reports"),
                    ("Cooldown", f"{REPORT_COOLDOWN//60} minutes between reports for the same user")
                ]
            elif value == "prison":
                embed.title = "🔓 PRISON SYSTEM"
                commands_list = [
                    ("Duration", "1 hour (unless manually released)"),
                    ("Effects", "Special role, nickname change, restricted permissions"),
                    ("Prison Role", PRISON_ROLE_NAME)
                ]
            
            for name, value in commands_list:
                embed.add_field(name=name, value=value, inline=False)
            
            await interaction.response.edit_message(embed=embed, view=view)
        
        select.callback = select_callback
        view.add_item(select)
        
        initial_embed = discord.Embed(
            title="🆘 HELP MENU",
            description="Select a category from the dropdown below",
            color=discord.Color.blue()
        )
        initial_embed.add_field(
            name="How to Use",
            value="1️⃣ Select a category\n2️⃣ View commands/info\n3️⃣ Use commands in server",
            inline=False
        )
        
        help_msg = await ctx.send(embed=initial_embed, view=view)
        await asyncio.sleep(300)  # Auto-delete after 5 minutes
        try:
            await help_msg.delete()
        except:
            pass

async def setup(bot):
    await bot.add_cog(UserCommands(bot))