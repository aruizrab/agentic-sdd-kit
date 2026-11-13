//! Crate docs are comments.

/// Doc comment, not code.
pub fn alpha(x: u32) -> u32 {
    let s = "}{ not a brace";
    // a comment with } and {
    let c = '}';
    x + 1
}

pub(crate) async fn generic<T: Clone>(v: T) -> Result<Vec<u8>, E> where T: Clone {
    /* block
       comment } */
    Ok(vec![])
}

struct S;

impl S {
    fn method(&self) -> &'static str {
        fn nested() {}
        "x"
    }
}

trait T {
    fn decl(&self);
}

#[cfg(test)]
mod tests {
    #[test]
    fn t() {
        assert!(true);
    }
}
