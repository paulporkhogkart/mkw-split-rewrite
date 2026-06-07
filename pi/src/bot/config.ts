export type BotConfig = {
  token: string;
  channelId: string;
  guildId: string | null;
  dbPath: string;
  wsUrl: string;
};

export function loadConfig(env: NodeJS.ProcessEnv = process.env): BotConfig {
  const token = env.DISCORD_BOT_TOKEN ?? '';
  const channelId = env.DISCORD_CHANNEL_ID ?? '';
  if (!token) throw new Error('DISCORD_BOT_TOKEN is required');
  if (!channelId) throw new Error('DISCORD_CHANNEL_ID is required');
  const port = env.PORT ?? '8787';
  return {
    token,
    channelId,
    guildId: env.DISCORD_GUILD_ID ?? null,
    dbPath: env.MKW_DB ?? 'mkw.db',
    wsUrl: env.BOT_WS_URL ?? `ws://127.0.0.1:${port}/v1/events`,
  };
}
