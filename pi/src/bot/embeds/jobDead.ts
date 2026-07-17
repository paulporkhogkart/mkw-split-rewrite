import { EmbedBuilder } from 'discord.js';

export type JobDeadData = { course: string; holder: string | null; record_str: string;
  reason: string; attempts: number };

/** Red alert: a WR trail job exhausted its attempts (or hit a terminal time_mismatch)
 *  and will never retry on its own. Needs a human — or, for a mislinked video, a
 *  corrected link on mkwrs (reconcile revives the job automatically when the link
 *  changes). */
export function buildJobDeadEmbed(d: JobDeadData): EmbedBuilder {
  return new EmbedBuilder()
    .setColor(0xef4444)
    .setTitle('WR TRAIL JOB DEAD')
    .setDescription(`Trail extraction for **${d.course}** (${d.record_str}${d.holder ? ` by ${d.holder}` : ''}) gave up and will not retry on its own.`)
    .addFields(
      { name: 'Last error', value: `\`${d.reason.slice(0, 200)}\``, inline: false },
      { name: 'Attempts', value: String(d.attempts), inline: true },
    )
    .setFooter({ text: d.reason.startsWith('time_mismatch')
      ? 'Likely a wrong/mislinked video — revives automatically if mkwrs corrects the link. npm run wr-flags lists it.'
      : 'npm run wr-flags lists dead jobs.' });
}
