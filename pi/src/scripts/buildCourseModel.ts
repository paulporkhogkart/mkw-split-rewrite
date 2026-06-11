// pi/src/scripts/buildCourseModel.ts
import { openDb, applySchema } from '../db/connect';
import { rebuildCourseModel } from '../db/courseModels';

const arg = (k: string, d?: string) => {
  const i = process.argv.indexOf(k); return i >= 0 ? process.argv[i + 1] : d;
};

function main() {
  const db = openDb(process.env.MKW_DB ?? 'mkw.db');
  applySchema(db);
  const slug = arg('--course');
  const cc = Number(arg('--cc', '150'));
  const window = Number(arg('--window', '40'));
  if (!slug) { console.error('usage: build-course-model --course <slug> [--cc 150] [--window 40]'); process.exitCode = 1; return; }

  const course = db.prepare('SELECT id FROM courses WHERE slug=?').get(slug) as { id: number } | undefined;
  if (!course) { console.error(`unknown course: ${slug}`); process.exitCode = 1; return; }

  const res = rebuildCourseModel(db, course.id, cc, window);
  if (!res) { console.error(`no usable runs for ${slug}`); process.exitCode = 1; return; }
  console.log(`[course-model] ${slug} cc${cc}: ${res.status}, ${res.laps} laps, ${res.runs} runs`);
}
main();
