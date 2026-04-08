#include <iostream>
int main() {
    uint16_t len = 0;
    while(len--) {
        std::cout << "loop" << std::endl;
    }
    std::cout << "done, len=" << len << std::endl;
}
