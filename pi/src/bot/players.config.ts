export const ID_TO_NAME: Record<string, string> = {
  '477788220982296576': 'Gub',
  '1213316126948335636': 'paul pork',   // must match players.display_name (renamed from 'Paul')
  '267421165625147392': 'Aliias',
  '867421622347890719': 'Luke',
};

export const NAME_TO_ID: Record<string, string> =
  Object.fromEntries(Object.entries(ID_TO_NAME).map(([id, name]) => [name, id]));

export const THUMBNAIL_GIFS: Record<string, string[]> = {
  'paul pork': [   // keyed by DB display_name (gifFor is called with the player's display_name)
    'https://i.imgur.com/K9Qu1XM.gif', 'https://i.imgur.com/Wepl7A2.gif', 'https://i.imgur.com/kqiz9rj.gif',
    'https://i.imgur.com/oYFbGcD.gif', 'https://i.imgur.com/h3c0sli.gif', 'https://i.imgur.com/cBKo7cG.gif',
    'https://i.imgur.com/YHwWXsf.gif', 'https://i.imgur.com/aA4Gl9f.gif', 'https://i.imgur.com/DQxOwCS.gif',
    'https://i.imgur.com/JGHkIIS.gif', 'https://i.imgur.com/FtFhD6a.gif', 'https://i.imgur.com/ALgqFVz.gif',
    'https://i.imgur.com/7vjuvuq.gif',
  ],
  Aliias: ['https://i.imgur.com/lfS1SkJ.gif', 'https://i.imgur.com/l5eJXfl.gif', 'https://i.imgur.com/eiHaLw6.gif', 'https://i.imgur.com/KV8VW7x.gif'],
  Luke: ['https://i.imgur.com/PcksQkq.gif', 'https://i.imgur.com/YadWWyh.gif', 'https://i.imgur.com/dK3KtfE.gif', 'https://i.imgur.com/SYlI3Tg.gif'],
  Gub: ['https://i.imgur.com/3u7SCNw.gif', 'https://i.imgur.com/nARULQI.gif'],
};

// TEMP (2026-06-23): Gub's thumbnail is pinned to a single static image instead of its GIFs
// above; everyone else keeps their GIFs. To restore, delete TEMP_THUMBNAILS and the override
// line in gifFor().
const TEMP_THUMBNAILS: Record<string, string> = {
  Gub: 'https://i.imgur.com/FLwnRZ5.png',
};

/** Random GIF for a player, or null when none is configured (legacy KeyError fix). */
export function gifFor(name: string): string | null {
  if (TEMP_THUMBNAILS[name]) return TEMP_THUMBNAILS[name];   // TEMP: pinned static thumbnail
  const list = THUMBNAIL_GIFS[name];
  return list && list.length ? list[Math.floor(Math.random() * list.length)] : null;
}

export function nameForId(id: string): string | null {
  return ID_TO_NAME[id] ?? null;
}
