import { EmbedBuilder } from 'discord.js';

export type NameFlagData = {
  category: string; raw_value: string; slug_guess: string | null; course: string | null;
};

/** Amber alert: mkwrs used a name we cannot map to a canonical slug. WR trail processing
 *  for that record is blocked until someone maps it, so this needs a human. */
export function buildNameFlagEmbed(d: NameFlagData): EmbedBuilder {
  const e = new EmbedBuilder()
    .setColor(0xf59e0b)
    .setTitle('UNMAPPED mkwrs NAME')
    .setDescription(`A **${d.category}** name from mkwrs did not resolve to a known slug. WR processing for that record is blocked until it is mapped.`)
    .addFields(
      { name: 'Raw value', value: `\`${d.raw_value}\``, inline: true },
      { name: 'Slug guess', value: d.slug_guess ? `\`${d.slug_guess}\`` : '—', inline: true },
    );
  if (d.course) e.addFields({ name: 'Seen on', value: d.course, inline: true });
  return e.setFooter({ text: d.category === 'course'
    ? 'Map it in wr/courses.ts (MKWRS_ALIASES), then: npm run wr-flags'
    : 'Map it in wr/roster.ts (canonical set or alias map), then: npm run wr-flags' });
}
