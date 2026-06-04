export function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/['']/g, '')      // drop straight + curly apostrophes
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}
