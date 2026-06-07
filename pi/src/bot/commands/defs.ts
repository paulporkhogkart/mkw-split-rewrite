import { SlashCommandBuilder } from 'discord.js';

export const commandDefs = [
  new SlashCommandBuilder()
    .setName('leaderboard')
    .setDescription('Show a track or overall leaderboard')
    .addStringOption((o) =>
      o.setName('track').setDescription('Track name').setRequired(false).setAutocomplete(true),
    ),
  new SlashCommandBuilder()
    .setName('wr')
    .setDescription('Show the current world record for a track')
    .addStringOption((o) =>
      o.setName('track').setDescription('Track name').setRequired(true).setAutocomplete(true),
    ),
  new SlashCommandBuilder()
    .setName('nemesis')
    .setDescription('Tracks where you are furthest behind')
    .addStringOption((o) =>
      o
        .setName('player')
        .setDescription('Compare vs a specific player')
        .setRequired(false)
        .setAutocomplete(true),
    ),
].map((c) => c.toJSON());

/** Filter helper for autocomplete: case-insensitive substring, capped at 25 (Discord limit). */
export function filterChoices(
  values: string[],
  current: string,
): { name: string; value: string }[] {
  const q = current.toLowerCase();
  return values
    .filter((v) => v.toLowerCase().includes(q))
    .slice(0, 25)
    .map((v) => ({ name: v, value: v }));
}
