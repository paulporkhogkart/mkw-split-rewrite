import { EmbedBuilder } from 'discord.js';

export type JobStuckData = { course: string; holder: string | null; record_str: string;
  reason: string; attempts: number };

/** A WR trail job keeps failing. NOT dead: it stays queued and retries on an escalating
 *  cooldown (hourly up to daily) whenever a worker PC is online. The one exception is
 *  time_mismatch — the video itself is wrong for the record, so it is parked until the
 *  mkwrs link changes (reconcile revives it automatically). Amber for retrying, red for
 *  parked. */
export function buildJobStuckEmbed(d: JobStuckData): EmbedBuilder {
  const parked = d.reason.startsWith('time_mismatch');
  return new EmbedBuilder()
    .setColor(parked ? 0xef4444 : 0xf59e0b)
    .setTitle('WR TRAIL JOB STUCK')
    .setDescription(`Trail extraction for **${d.course}** (${d.record_str}${d.holder ? ` by ${d.holder}` : ''}) has failed ${d.attempts} time(s). ${parked
      ? 'Parked: the video does not match the record.'
      : 'It stays queued and will keep retrying on a cooldown whenever a tracker PC is online.'}`)
    .addFields(
      { name: 'Last error', value: `\`${d.reason.slice(0, 200)}\``, inline: false },
      { name: 'Attempts', value: String(d.attempts), inline: true },
    )
    .setFooter({ text: parked
      ? 'Likely a wrong/mislinked video; revives automatically if mkwrs corrects the link. npm run wr-flags lists it.'
      : 'npm run wr-flags lists stuck jobs.' });
}
