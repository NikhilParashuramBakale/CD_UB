#include <stdio.h>

/*
 * Sample for LLVM UB Analyzer:
 * Signed integer overflow in C is undefined behavior.
 * Optimizers may assume overflow never happens and transform logic accordingly.
 */
int check_overflow_assumption(int x) {
    int y = x + 1;

    /*
     * If x == INT_MAX, y overflows (UB).
     * Under optimization, compiler may simplify based on "no UB" assumption.
     */
    if (y < x) {
        return 1;
    }

    return 0;
}

int main(void) {
    int inputs[] = {0, 1, 2147483647};
    for (int i = 0; i < 3; i++) {
        printf("x=%d -> check=%d\n", inputs[i], check_overflow_assumption(inputs[i]));
    }
    return 0;
}
