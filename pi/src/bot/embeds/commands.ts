import { EmbedBuilder } from 'discord.js';
import { formatDuration } from '../format';
import type { NemesisRow } from '../format';
import { formatNemesisTracks } from '../format';

const BLUE = 0xc2ddfd;

function reignFooter(e: EmbedBuilder, leader: string | null, reignMs: number | null, thumb: string | null) {
  if (leader && reignMs != null) {
    e.setFooter({ text: `BEHOLD THE ${formatDuration(reignMs)} REIGN OF ${leader.toUpperCase()}`, ...(thumb ? { iconURL: thumb } : {}) });
    if (thumb) e.setThumbnail(thumb);
  }
}

type BoardView = { title: string; body: string; leader: string | null; reign_ms: number | null; color?: number | null };

export function trackLeaderboardEmbed(v: BoardView, thumb: string | null): EmbedBuilder {
  const e = new EmbedBuilder().setTitle(v.title).setColor(v.color ?? BLUE).setDescription(v.body);
  reignFooter(e, v.leader, v.reign_ms, thumb);
  return e;
}

export function totalLeaderboardEmbed(v: BoardView, thumb: string | null): EmbedBuilder {
  const e = new EmbedBuilder().setTitle(v.title).setColor(v.color ?? BLUE).setDescription(v.body);
  reignFooter(e, v.leader, v.reign_ms, thumb);
  return e;
}

export function wrInfoEmbed(v: { title: string; time: string; char: string; kart: string; reign_ms: number | null }): EmbedBuilder {
  const e = new EmbedBuilder().setTitle(v.title).setColor(BLUE)
    .addFields(
      { name: 'TIME', value: `\`${v.time}\``, inline: true },
      { name: 'CHAR', value: `\`${v.char}\``, inline: true },
      { name: 'KART', value: `\`${v.kart}\``, inline: true },
    );
  if (v.reign_ms != null) e.setFooter({ text: `${formatDuration(v.reign_ms).toUpperCase()} REIGN` });
  return e;
}

export function nemesisPageEmbed(title: string, rows: NemesisRow[], targeted: boolean, startPosition: number, footer: string, color: number | null = null): EmbedBuilder {
  return new EmbedBuilder().setTitle(title).setColor(color ?? BLUE)
    .setDescription(formatNemesisTracks(rows, targeted, startPosition)).setFooter({ text: footer });
}
