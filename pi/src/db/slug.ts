export function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[‘’']/g, '')   // drop apostrophes: straight ' and curly U+2018/U+2019
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}
