// Flow corpus (JavaScript). Top-level functions with a known call graph.
// Ground truth call edges are in ../ground_truth.json. Every callee has a single
// definition so name-based resolution is unambiguous.

function handleRequest() {
  validateInput();
  const result = compute();
  writeResponse(result);
}

function validateInput() {
  checkSchema();
  checkLimits();
}

function checkSchema() {
  normalize();
}

function checkLimits() {
  normalize();
}

function normalize() {
  return 1;
}

function compute() {
  const total = aggregate();
  return finalize(total);
}

function aggregate() {
  loadRows();
  return reduceRows();
}

function loadRows() {
  normalize();
}

function reduceRows() {
  return 2;
}

function finalize(total) {
  writeLog();
  return total;
}

function writeResponse(result) {
  writeLog();
}

function writeLog() {
  return null;
}

function unrelatedHelper() {
  return 42;
}
