/** Doc comment, not code. */
export function Comp({ title, children }) {
  const s = "} {";
  return s;
}

const f = ({ a }) => {
  return a;
};

export const g = async (x: number): Promise<number> => {
  return x;
};

const expr = (a) => a + 1;

function typed(o: { a: number }): void {
  console.log(o);
}

function retLiteral(): { a: number } {
  return { a: 1 };
}

class K {
  private value = 1;

  method(x: string): string {
    if (x) {
      return x;
    }
    for (const c of x) {
      console.log(c);
    }
    while (false) {
    }
    switch (x) {
      default:
        break;
    }
    try {
      foo();
    } catch (e) {
      bar();
    }
    return `template ${x} }`;
  }

  static async load<T>(id: T) {
    return id;
  }
}

interface I {
  a(): void;
  b(): string
}
