const fs = require("fs");
const path = require("path");
const zlib = require("zlib");

const SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

function crcTable() {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
}

const CRC_TABLE = crcTable();

function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function readPng(file) {
  const buf = fs.readFileSync(file);
  if (!buf.subarray(0, 8).equals(SIGNATURE)) throw new Error(`Not a PNG: ${file}`);

  let offset = 8;
  let width = 0;
  let height = 0;
  const idat = [];

  while (offset < buf.length) {
    const length = buf.readUInt32BE(offset);
    const type = buf.subarray(offset + 4, offset + 8).toString("ascii");
    const data = buf.subarray(offset + 8, offset + 8 + length);
    offset += 12 + length;

    if (type === "IHDR") {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      const bitDepth = data[8];
      const colorType = data[9];
      const interlace = data[12];
      if (bitDepth !== 8 || colorType !== 6 || interlace !== 0) {
        throw new Error(`Unsupported PNG format in ${file}; expected 8-bit RGBA non-interlaced`);
      }
    } else if (type === "IDAT") {
      idat.push(data);
    } else if (type === "IEND") {
      break;
    }
  }

  const raw = zlib.inflateSync(Buffer.concat(idat));
  const stride = width * 4;
  const pixels = Buffer.alloc(width * height * 4);
  let src = 0;
  let prev = Buffer.alloc(stride);

  for (let y = 0; y < height; y++) {
    const filter = raw[src++];
    const row = raw.subarray(src, src + stride);
    src += stride;
    const out = Buffer.alloc(stride);

    for (let x = 0; x < stride; x++) {
      const left = x >= 4 ? out[x - 4] : 0;
      const up = prev[x];
      const upLeft = x >= 4 ? prev[x - 4] : 0;
      let value;
      if (filter === 0) value = row[x];
      else if (filter === 1) value = row[x] + left;
      else if (filter === 2) value = row[x] + up;
      else if (filter === 3) value = row[x] + Math.floor((left + up) / 2);
      else if (filter === 4) {
        const p = left + up - upLeft;
        const pa = Math.abs(p - left);
        const pb = Math.abs(p - up);
        const pc = Math.abs(p - upLeft);
        const pr = pa <= pb && pa <= pc ? left : pb <= pc ? up : upLeft;
        value = row[x] + pr;
      } else {
        throw new Error(`Unsupported PNG filter ${filter} in ${file}`);
      }
      out[x] = value & 0xff;
    }

    out.copy(pixels, y * stride);
    prev = out;
  }

  return { width, height, pixels };
}

function chunk(type, data) {
  const typeBuf = Buffer.from(type, "ascii");
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])));
  return Buffer.concat([len, typeBuf, data, crc]);
}

function writePng(file, png) {
  const stride = png.width * 4;
  const raw = Buffer.alloc((stride + 1) * png.height);
  for (let y = 0; y < png.height; y++) {
    raw[y * (stride + 1)] = 0;
    png.pixels.copy(raw, y * (stride + 1) + 1, y * stride, (y + 1) * stride);
  }

  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(png.width, 0);
  ihdr.writeUInt32BE(png.height, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;

  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(
    file,
    Buffer.concat([SIGNATURE, chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]),
  );
}

function alphaOver(bottom, top) {
  if (bottom.width !== top.width || bottom.height !== top.height) throw new Error("PNG sizes do not match");
  const out = Buffer.from(bottom.pixels);
  for (let i = 0; i < out.length; i += 4) {
    const sa = top.pixels[i + 3] / 255;
    if (sa === 0) continue;
    const da = out[i + 3] / 255;
    const oa = sa + da * (1 - sa);
    if (oa === 0) continue;
    out[i] = Math.round((top.pixels[i] * sa + out[i] * da * (1 - sa)) / oa);
    out[i + 1] = Math.round((top.pixels[i + 1] * sa + out[i + 1] * da * (1 - sa)) / oa);
    out[i + 2] = Math.round((top.pixels[i + 2] * sa + out[i + 2] * da * (1 - sa)) / oa);
    out[i + 3] = Math.round(oa * 255);
  }
  return { width: bottom.width, height: bottom.height, pixels: out };
}

function crop(png, x, y, width, height) {
  const out = Buffer.alloc(width * height * 4);
  for (let row = 0; row < height; row++) {
    png.pixels.copy(out, row * width * 4, ((y + row) * png.width + x) * 4, ((y + row) * png.width + x + width) * 4);
  }
  return { width, height, pixels: out };
}

const root = path.join(__dirname, "..", "assets", "lpc-character-bases", "selected-base-sheets");
const outDir = path.join(root, "headed-walk-only");

const pairs = [
  ["female_human_walk.png", "bodies/female_body_light_universal.png", "heads/human_female_head_universal.png"],
  ["male_human_walk.png", "bodies/male_body_light_universal.png", "heads/human_male_head_universal.png"],
  ["female_orc_walk.png", "bodies/female_body_green_universal.png", "heads/orc_female_head_universal.png"],
  ["male_orc_walk.png", "bodies/male_body_green_universal.png", "heads/orc_male_head_universal.png"],
  ["male_lizard_walk.png", "bodies/male_body_green_universal.png", "heads/lizard_male_head_universal.png"],
  ["female_lizard_walk.png", "bodies/female_body_green_universal.png", "heads/lizard_female_head_universal.png"],
  ["male_wolf_walk.png", "bodies/male_body_green_universal.png", "heads/wolf_male_head_universal.png"],
  ["female_wolf_walk.png", "bodies/female_body_green_universal.png", "heads/wolf_female_head_universal.png"],
  ["minotaur_walk.png", "bodies/muscular_body_light_universal.png", "heads/minotaur_head_universal.png"],
  ["boarman_walk.png", "bodies/male_body_green_universal.png", "heads/boarman_head_universal.png"],
  ["zombie_walk.png", "bodies/zombie_body_universal.png", "heads/zombie_head_universal.png"],
  ["skeleton_walk.png", "bodies/skeleton_body_universal.png", "heads/skeleton_head_universal.png"],
];

for (const [name, bodyRel, headRel] of pairs) {
  const body = readPng(path.join(root, bodyRel));
  const head = readPng(path.join(root, headRel));
  const combined = alphaOver(body, head);
  const walkOnly = crop(combined, 0, 64 * 8, 64 * 9, 64 * 4);
  writePng(path.join(outDir, name), walkOnly);
}

console.log(`Wrote ${pairs.length} headed walk-only sheets to ${outDir}`);
