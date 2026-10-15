// Dependency-free preflight: runnable before npm ci.
const fs = require('node:fs');
const path = require('node:path');

function supportsNode(version, range) {
  const match = /^v?(\d+)\.(\d+)\.(\d+)$/.exec(version);
  if (!match) return false;
  const current = match.slice(1).map(Number);
  const compare = (a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
  return range.split(' || ').some(clause => {
    const required = /^(\^|>=)(\d+)\.(\d+)\.(\d+)$/.exec(clause);
    if (!required) throw new Error('Unsupported Node engine expression');
    const minimum = required.slice(2).map(Number);
    const maximum = minimum[0] ? [minimum[0] + 1, 0, 0]
      : minimum[1] ? [0, minimum[1] + 1, 0] : [0, 0, minimum[2] + 1];
    return compare(current, minimum) >= 0 && (required[1] === '>=' || compare(current, maximum) < 0);
  });
}

if (require.main === module) {
  const range = JSON.parse(fs.readFileSync(path.join(__dirname, 'package.json'), 'utf8')).engines.node;
  if (!supportsNode(process.version, range)) {
    console.error(`Unsupported Node.js ${process.version}; frontend builds require ${range}.`);
    console.error('Select Node.js 24 before retrying: nvm install && nvm use (the repository includes .nvmrc).');
    console.error('If you do not use nvm, install Node.js 24 with your usual version manager. No cloud access was attempted.');
    process.exitCode = 2;
  }
}

module.exports = { supportsNode };
