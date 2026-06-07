import { EmbedBuilder } from 'discord.js';
import type { WrEmbedData } from '../types';
import { formatDuration } from '../format';

/** Grey WR embed — ports legacy DiscordBot._send_wr_message. */
export function buildWrEmbed(d: WrEmbedData): EmbedBuilder {
  const e = new EmbedBuilder()
    .setTitle(`WORLD RECORD BY ${d.holder.toUpperCase()}`)
    .setColor(0xf3f3f3)
    .addFields(
      { name: 'TRACK', value: `\`${d.track}\``, inline: true },
      { name: 'TIME', value: `\`${d.record}\``, inline: true },
      { name: 'DELTA', value: `\`${d.improvement_str ?? 'First WR'}\``, inline: true },
    );
  if (d.reign && d.reign.reign_ms != null) {
    const dur = formatDuration(d.reign.reign_ms);
    const prev = (d.reign.previous_holder ?? '').toUpperCase();
    e.setFooter({ text: d.reign.is_same_person ? `THE ${dur} REIGN OF ${prev} CONTINUES` : `THE ${dur} REIGN OF ${prev} IS OVER` });
  }
  return e;
}
