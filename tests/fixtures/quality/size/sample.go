// Package sample has doc comments.
package sample

// F has a doc comment.
func F(m map[string]interface{}) error {
	s := "}{"
	_ = s
	return nil
}

func G() interface{ M() } {
	return nil
}

type R struct{}

func (r *R) Method(x int) int {
	f := func() int {
		return x
	}
	return f()
}

func Map[T any](xs []T) []T {
	r := '}'
	_ = r
	return xs
}
