import { describe, it, expect } from 'vitest';
import { trackLeaderboardEmbed, totalLeaderboardEmbed, wrInfoEmbed, nemesisPageEmbed } from './commands';

const THREE_DAYS_MS = 3 * 86400_000;

describe('trackLeaderboardEmbed', () => {
  it('sets title, color, description', () => {
    const v = { title: 'Rainbow Road Leaderboard', body: '`1. Paul  1:46.000`', leader: null, reign_ms: null };
    const e = trackLeaderboardEmbed(v, null).toJSON();
    expect(e.title).toBe('Rainbow Road Leaderboard');
    expect(e.color).toBe(0xc2ddfd);
    expect(e.description).toBe('`1. Paul  1:46.000`');
    expect(e.footer).toBeUndefined();
  });

  it('adds reign footer with thumbnail when leader + reign_ms present', () => {
    const v = { title: 'DK Pass Leaderboard', body: '`1. Paul  1:00.000`', leader: 'Paul', reign_ms: THREE_DAYS_MS };
    const e = trackLeaderboardEmbed(v, 'http://paul.gif').toJSON();
    expect(e.footer?.text).toBe('BEHOLD THE 3 DAY REIGN OF PAUL');
    expect(e.footer?.icon_url).toBe('http://paul.gif');
    expect(e.thumbnail?.url).toBe('http://paul.gif');
  });

  it('omits footer when leader is null', () => {
    const v = { title: 'DK Pass Leaderboard', body: '`1. Paul`', leader: null, reign_ms: THREE_DAYS_MS };
    const e = trackLeaderboardEmbed(v, 'http://paul.gif').toJSON();
    expect(e.footer).toBeUndefined();
  });

  it('omits footer when reign_ms is null', () => {
    const v = { title: 'DK Pass Leaderboard', body: '`1. Paul`', leader: 'Paul', reign_ms: null };
    const e = trackLeaderboardEmbed(v, null).toJSON();
    expect(e.footer).toBeUndefined();
  });

  it('sets footer without iconURL when thumb is null', () => {
    const v = { title: 'DK Pass Leaderboard', body: '`1. Paul`', leader: 'Paul', reign_ms: THREE_DAYS_MS };
    const e = trackLeaderboardEmbed(v, null).toJSON();
    expect(e.footer?.text).toBe('BEHOLD THE 3 DAY REIGN OF PAUL');
    expect(e.footer?.icon_url).toBeUndefined();
  });
});

describe('totalLeaderboardEmbed', () => {
  it('sets title, color, description', () => {
    const v = { title: 'Overall Leaderboard', body: '`1. Paul  3:30.000`', leader: null, reign_ms: null };
    const e = totalLeaderboardEmbed(v, null).toJSON();
    expect(e.title).toBe('Overall Leaderboard');
    expect(e.color).toBe(0xc2ddfd);
    expect(e.description).toBe('`1. Paul  3:30.000`');
    expect(e.footer).toBeUndefined();
  });

  it('adds reign footer BEHOLD THE ... REIGN OF <UPPERCASED LEADER>', () => {
    const v = { title: 'Overall Leaderboard', body: '`1. Paul`', leader: 'Paul', reign_ms: THREE_DAYS_MS };
    const e = totalLeaderboardEmbed(v, 'http://paul.gif').toJSON();
    expect(e.footer?.text).toBe('BEHOLD THE 3 DAY REIGN OF PAUL');
    expect(e.footer?.icon_url).toBe('http://paul.gif');
    expect(e.thumbnail?.url).toBe('http://paul.gif');
  });
});

describe('wrInfoEmbed', () => {
  it('sets title, color, TIME/CHAR/KART fields (inline), and REIGN footer', () => {
    const v = { title: "Paul's Rainbow Road", time: '1:39.000', char: 'Peach', kart: 'Boo Pipes', reign_ms: THREE_DAYS_MS };
    const e = wrInfoEmbed(v).toJSON();
    expect(e.title).toBe("Paul's Rainbow Road");
    expect(e.color).toBe(0xc2ddfd);
    expect(e.fields).toEqual([
      { name: 'TIME', value: '`1:39.000`', inline: true },
      { name: 'CHAR', value: '`Peach`', inline: true },
      { name: 'KART', value: '`Boo Pipes`', inline: true },
    ]);
    expect(e.footer?.text).toBe('3 DAY REIGN');
  });

  it('omits footer when reign_ms is null', () => {
    const v = { title: "Paul's DK Pass", time: '1:00.000', char: 'Mario', kart: 'Pipe Frame', reign_ms: null };
    const e = wrInfoEmbed(v).toJSON();
    expect(e.footer).toBeUndefined();
  });
});

describe('nemesisPageEmbed', () => {
  it('sets title, color, formatted description, and page footer', () => {
    const rows = [
      { track_name: 'Rainbow Road', time_difference_str: '+2.500s', ahead_player: 'Luke' },
      { track_name: 'DK Pass', time_difference_str: '+0.300s', ahead_player: 'Paul' },
    ];
    const e = nemesisPageEmbed("Paul's Nemesis Tracks", rows, false, 1, 'Page 1 of 2 • 7 total tracks').toJSON();
    expect(e.title).toBe("Paul's Nemesis Tracks");
    expect(e.color).toBe(0xc2ddfd);
    expect(e.description).toBe(
      '`1. Rainbow Road  (+2.500s) [Luke]`\n' +
      '`2. DK Pass       (+0.300s) [Paul]`'
    );
    expect(e.footer?.text).toBe('Page 1 of 2 • 7 total tracks');
  });

  it('handles targeted (no [player] suffix) starting from position 6', () => {
    const rows = [
      { track_name: 'DK Pass', time_difference_str: '+0.300s', ahead_player: 'Luke' },
    ];
    const e = nemesisPageEmbed("Paul's Nemesis Tracks vs Luke", rows, true, 6, '6 tracks').toJSON();
    expect(e.description).toBe('`6. DK Pass  (+0.300s)`');
    expect(e.footer?.text).toBe('6 tracks');
  });
});

describe('player colours', () => {
  it('leaderboard embeds use the view colour, else default blue', () => {
    const v = { title: 'X', body: '`x`', leader: null, reign_ms: null, color: 0x123456 };
    expect(trackLeaderboardEmbed(v, null).toJSON().color).toBe(0x123456);
    expect(totalLeaderboardEmbed(v, null).toJSON().color).toBe(0x123456);
    expect(trackLeaderboardEmbed({ ...v, color: null }, null).toJSON().color).toBe(0xc2ddfd);
  });
  it('nemesisPageEmbed uses the colour arg, else default blue', () => {
    expect(nemesisPageEmbed('T', [], false, 1, 'f', 0x123456).toJSON().color).toBe(0x123456);
    expect(nemesisPageEmbed('T', [], false, 1, 'f').toJSON().color).toBe(0xc2ddfd);
  });
});
