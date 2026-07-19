import type { DatabaseSync } from 'node:sqlite';
import type { EmbedBuilder } from 'discord.js';
import type { ServerEvent } from '../db/types';
import { buildPbData, buildWrData } from './enrich';
import { buildPbEmbed } from './embeds/pb';
import { buildWrEmbed } from './embeds/wr';
import { buildNameFlagEmbed } from './embeds/nameFlag';
import { buildJobStuckEmbed } from './embeds/jobStuck';
import { gifFor } from './players.config';

/** Build + emit the right embed for the events we announce; ignore the rest. One failure
 *  logs and does not throw (so a bad event can't take down the stream). */
export function dispatch(db: DatabaseSync, ev: ServerEvent, send: (e: EmbedBuilder) => void): void {
  try {
    if (ev.type === 'pb_achieved') {
      const d = buildPbData(db, ev);
      const footerIcon = d.still_ahead ? gifFor(d.still_ahead.name === 'WR' ? d.player : d.still_ahead.name) : null;
      send(buildPbEmbed(d, { thumbnail: gifFor(d.player), footerIcon }));
    } else if (ev.type === 'wr_update') {
      send(buildWrEmbed(buildWrData(db, ev)));
    } else if (ev.type === 'wr_name_flag') {
      send(buildNameFlagEmbed(ev));
    } else if (ev.type === 'wr_job_stuck') {
      send(buildJobStuckEmbed({ course: ev.course, holder: ev.holder,
        record_str: ev.record_str, reason: ev.reason, attempts: ev.attempts }));
    }
  } catch (err) {
    console.error('[bot] dispatch failed', err);
  }
}
