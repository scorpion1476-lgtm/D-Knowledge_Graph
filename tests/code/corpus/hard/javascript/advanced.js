const add = (a, b) => a + b;

const helpers = {
  double(value) {
    return add(value, value);
  },
  triple: function (value) {
    return add(value, add(value, value));
  },
};

async function loadAll(urls) {
  return Promise.all(urls);
}

function* counter() {
  yield 1;
}

export default function main() {
  return add(1, 2);
}

class Store {
  static create() {
    return new Store();
  }
  get size() {
    return 0;
  }
  #secret() {
    return 1;
  }
}
