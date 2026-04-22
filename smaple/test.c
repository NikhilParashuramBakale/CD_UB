#include <limits.h>
#include <stdio.h>

int main() {
    int x = INT_MAX;
    int y = x + 1; // UB
    printf("%d\n", y);
    return 0;
}
