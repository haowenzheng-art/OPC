import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const prismaClientPath = path.resolve(__dirname, '../../../node_modules/.prisma/client/index.js');
const Prisma = require(prismaClientPath);

export default Prisma;
