#include <iostream>
#include <stdint.h>

#pragma pack(push, 1)
struct ll_fw_end {
    uint8_t type;
    uint16_t crc;
} __attribute__((packed));
#pragma pack(pop)

int main() {
    std::cout << sizeof(ll_fw_end) << std::endl;
}
