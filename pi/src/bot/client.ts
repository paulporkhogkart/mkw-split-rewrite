import { Client, GatewayIntentBits, type EmbedBuilder, type SendableChannels } from 'discord.js';

/** Owns the discord.js client. Buffers embeds that arrive before the gateway is ready and
 *  flushes them on clientReady (ports the legacy message_queue behaviour). */
export class Announcer {
  private client: Client;
  private channel: SendableChannels | null = null;
  private ready = false;
  private queue: EmbedBuilder[] = [];

  constructor(private token: string, private channelId: string) {
    this.client = new Client({ intents: [GatewayIntentBits.Guilds] });
    this.client.once('clientReady', async () => {
      const ch = await this.client.channels.fetch(this.channelId).catch(() => null);
      this.channel = ch && ch.isSendable() ? ch : null;
      if (!this.channel) console.error(`[bot] channel ${this.channelId} not found or not sendable`);
      this.ready = true;
      for (const e of this.queue) await this.post(e);
      this.queue = [];
      console.log(`[bot] logged in as ${this.client.user?.tag}`);
    });
  }

  async start(): Promise<void> { await this.client.login(this.token); }

  async send(embed: EmbedBuilder): Promise<void> {
    if (!this.ready || !this.channel) { this.queue.push(embed); return; }
    await this.post(embed);
  }

  private async post(embed: EmbedBuilder): Promise<void> {
    try { await this.channel!.send({ embeds: [embed] }); }
    catch (err) { console.error('[bot] send failed', err); }
  }
}
