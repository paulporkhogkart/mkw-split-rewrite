import { EmbedBuilder } from 'discord.js';
import type { PbEmbedData } from '../types';
import { formatDuration, formatOvertaken, formatPositions } from '../format';

/** PB title. Ending someone else's reign leads with the new name ("<NAME> ENDED THE <dur>
 *  REIGN OF <PREV>"); extending your own reign uses the classic "THE <dur> REIGN OF <NAME>
 *  CONTINUES"; everything else (incl. a first-ever time on a track) is just
 *  "<NAME> PERSONAL BEST". */
export function pbTitle(d: PbEmbedData): string {
  const name = d.player.toUpperCase();
  if (!d.is_new_track_record || !d.reign || d.reign.reign_ms == null) return `${name} PERSONAL BEST`;
  const dur = formatDuration(d.reign.reign_ms);
  const prev = (d.reign.previous_holder ?? '').toUpperCase();
  return d.reign.is_same_person
    ? `THE ${dur} REIGN OF ${name} CONTINUES`
    : `${name} ENDED THE ${dur} REIGN OF ${prev}`;
}

/** Green PB embed — ports legacy DiscordBot._send_pb_message. GIF urls are injected so the
 *  builder stays deterministic (random selection happens in dispatch). */
export function buildPbEmbed(d: PbEmbedData, gifs: { thumbnail?: string | null; footerIcon?: string | null } = {}): EmbedBuilder {
  const e = new EmbedBuilder().setTitle(pbTitle(d)).setColor(0x6cca5f);
  if (gifs.thumbnail) e.setThumbnail(gifs.thumbnail);
  e.addFields(
    // inline so TRACK/TIME/DELTA share one compact row (discord.py add_field defaults to
    // inline=True, which the legacy bot relied on; discord.js defaults to false).
    { name: 'TRACK', value: `\`${d.track}\``, inline: true },
    { name: 'TIME', value: `\`${d.time}\``, inline: true },
    { name: 'DELTA', value: `\`${d.improvement_str}\``, inline: true },
    { name: 'OVERTOOK', value: formatOvertaken(d.overtaken), inline: true },
    { name: 'POSITION', value: formatPositions(d.positions), inline: true },
  );
  if (d.still_ahead) {
    const aheadName = d.still_ahead.name === 'WR' ? 'The WR' : d.still_ahead.name;
    e.setFooter({ text: `${aheadName} is still ahead! (${d.still_ahead.diff_str})`, ...(gifs.footerIcon ? { iconURL: gifs.footerIcon } : {}) });
  }
  return e;
}
