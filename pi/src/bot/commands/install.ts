import {
  Client,
  ButtonBuilder,
  ActionRowBuilder,
  ButtonStyle,
  ComponentType,
  MessageFlags,
  type ChatInputCommandInteraction,
  type AutocompleteInteraction,
} from 'discord.js';
import type { DatabaseSync } from 'node:sqlite';
import { commandDefs, filterChoices } from './defs';
import { buildTrackBoard, buildOverallBoard, buildWrInfo, buildNemesis } from './views';
import { trackLeaderboardEmbed, totalLeaderboardEmbed, wrInfoEmbed, nemesisPageEmbed } from '../embeds/commands';
import { listCourses, listPlayers } from '../../db/lookups';
import { activeSeasonId } from '../../db/seasons';
import { gifFor } from '../players.config';

const PAGE = 5;

export function installCommands(
  client: Client,
  db: DatabaseSync,
  opts: { guildId: string | null },
): void {
  client.once('clientReady', async () => {
    try {
      await (opts.guildId
        ? client.application!.commands.set(commandDefs, opts.guildId)
        : client.application!.commands.set(commandDefs));
      console.log(
        `[bot] registered ${commandDefs.length} commands${opts.guildId ? ` to guild ${opts.guildId}` : ' globally'}`,
      );
    } catch (e) {
      console.error('[bot] command registration failed', e);
    }
  });

  client.on('interactionCreate', async (interaction) => {
    try {
      if (interaction.isChatInputCommand()) {
        if (interaction.commandName === 'leaderboard') await handleLeaderboard(db, interaction);
        else if (interaction.commandName === 'wr') await handleWr(db, interaction);
        else if (interaction.commandName === 'nemesis') await handleNemesis(db, interaction);
      } else if (interaction.isAutocomplete()) {
        await handleAutocomplete(db, interaction);
      }
    } catch (e) {
      console.error('[bot] interaction failed', e);
    }
  });
}

async function handleAutocomplete(db: DatabaseSync, interaction: AutocompleteInteraction): Promise<void> {
  const focused = interaction.options.getFocused(true);
  const values =
    focused.name === 'player'
      ? listPlayers(db, activeSeasonId(db)).map((p) => p.display_name)
      : listCourses(db).map((c) => c.display_name);
  await interaction.respond(filterChoices(values, focused.value));
}

async function handleLeaderboard(db: DatabaseSync, interaction: ChatInputCommandInteraction): Promise<void> {
  const track = interaction.options.getString('track');
  if (track) {
    const v = buildTrackBoard(db, track);
    if ('error' in v) {
      await interaction.reply({ content: v.error, flags: MessageFlags.Ephemeral });
      return;
    }
    await interaction.reply({ embeds: [trackLeaderboardEmbed(v, v.leader ? gifFor(v.leader) : null)] });
  } else {
    const v = buildOverallBoard(db);
    await interaction.reply({ embeds: [totalLeaderboardEmbed(v, v.leader ? gifFor(v.leader) : null)] });
  }
}

async function handleWr(db: DatabaseSync, interaction: ChatInputCommandInteraction): Promise<void> {
  const track = interaction.options.getString('track', true);
  const v = buildWrInfo(db, track);
  if ('error' in v) {
    await interaction.reply({ content: v.error, flags: MessageFlags.Ephemeral });
    return;
  }
  await interaction.reply({ embeds: [wrInfoEmbed(v)] });
  if (v.video) {
    await interaction.followUp({
      content: v.video.note ? `${v.video.note}\n${v.video.url}` : v.video.url,
    });
  }
}

async function handleNemesis(db: DatabaseSync, interaction: ChatInputCommandInteraction): Promise<void> {
  const v = buildNemesis(db, interaction.user.id, interaction.options.getString('player'));
  if ('error' in v) {
    await interaction.reply({ content: v.error, flags: MessageFlags.Ephemeral });
    return;
  }

  const total = v.rows.length;
  const pageCount = Math.ceil(total / PAGE);

  const pageEmbed = (p: number) => {
    const slice = v.rows.slice(p * PAGE, p * PAGE + PAGE);
    const footer =
      pageCount > 1
        ? `Page ${p + 1} of ${pageCount} • ${total} total tracks`
        : `${total} tracks`;
    return nemesisPageEmbed(v.title, slice, v.targeted, p * PAGE + 1, footer);
  };

  if (pageCount === 1) {
    await interaction.reply({ embeds: [pageEmbed(0)] });
    return;
  }

  let page = 0;

  const row = (p: number) =>
    new ActionRowBuilder<ButtonBuilder>().addComponents(
      new ButtonBuilder()
        .setCustomId('nem_prev')
        .setLabel('◀')
        .setStyle(ButtonStyle.Secondary)
        .setDisabled(p === 0),
      new ButtonBuilder()
        .setCustomId('nem_next')
        .setLabel('▶')
        .setStyle(ButtonStyle.Secondary)
        .setDisabled(p >= pageCount - 1),
    );

  const response = await interaction.reply({ embeds: [pageEmbed(0)], components: [row(0)] });
  const collector = response.createMessageComponentCollector({
    componentType: ComponentType.Button,
    time: 300_000,
  });

  collector.on('collect', async (i) => {
    if (i.user.id !== interaction.user.id) {
      await i.reply({ content: 'Not your nemesis list.', flags: MessageFlags.Ephemeral });
      return;
    }
    page =
      i.customId === 'nem_next'
        ? Math.min(page + 1, pageCount - 1)
        : Math.max(page - 1, 0);
    await i.update({ embeds: [pageEmbed(page)], components: [row(page)] });
  });

  collector.on('end', async () => {
    try {
      await interaction.editReply({ components: [] });
    } catch {
      /* message gone */
    }
  });
}
