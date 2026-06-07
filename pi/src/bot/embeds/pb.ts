import { EmbedBuilder } from 'discord.js';
import type { PbEmbedData } from '../types';
import { formatDuration, formatOvertaken, formatPositions } from '../format';

/** PB title — ports legacy DiscordBot._generate_title with the "<NAME> PERSONAL BEST" change. */
export function pbTitle(d: PbEmbedData): string {
  if (!d.is_new_track_record) return `${d.player.toUpperCase()} PERSONAL BEST`;
  if (!d.reign || d.reign.reign_ms == null) return 'NEW TRACK RECORD';
  const dur = formatDuration(d.reign.reign_ms);
  const prev = (d.reign.previous_holder ?? '').toUpperCase();
  return d.reign.is_same_person ? `THE ${dur} REIGN OF ${prev} CONTINUES` : `THE ${dur} REIGN OF ${prev} IS OVER`;
}

/** Green PB embed — ports legacy DiscordBot._send_pb_message. GIF urls are injected so the
 *  builder stays deterministic (random selection happens in dispatch). */
export function buildPbEmbed(d: PbEmbedData, gifs: { thumbnail?: string | null; footerIcon?: string | null } = {}): EmbedBuilder {
  const e = new EmbedBuilder().setTitle(pbTitle(d)).setColor(0x6cca5f);
  if (gifs.thumbnail) e.setThumbnail(gifs.thumbnail);
  e.addFields(
    { name: 'TRACK', value: `\`${d.track}\`` },
    { name: 'TIME', value: `\`${d.time}\`` },
    { name: 'DELTA', value: `\`${d.improvement_str}\`` },
    { name: 'OVERTOOK', value: formatOvertaken(d.overtaken), inline: true },
    { name: 'POSITION', value: formatPositions(d.positions), inline: true },
  );
  if (d.still_ahead) {
    const aheadName = d.still_ahead.name === 'WR' ? 'The WR' : d.still_ahead.name;
    e.setFooter({ text: `${aheadName} is still ahead! (${d.still_ahead.diff_str})`, ...(gifs.footerIcon ? { iconURL: gifs.footerIcon } : {}) });
  }
  return e;
}
