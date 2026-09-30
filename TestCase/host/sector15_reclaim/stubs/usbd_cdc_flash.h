/*
 * Stands in for Core/Inc/usbd_cdc_flash.h, which drags in the HAL and the USB
 * stack. Only the sector address and the write prototype are used; the
 * prototype must stay identical to the real one.
 */

#ifndef HOSTTEST_USBD_CDC_FLASH_H_
#define HOSTTEST_USBD_CDC_FLASH_H_

#include <stdint.h>

#define IAP_STATE_SECTOR_ADDR ((uintptr_t)fake_sector)
#define RESERVED_TAIL_SECTORS 1

uint16_t Flash_If_Write(uint8_t *DataAddress, uint8_t *FlashAddress, uint32_t Len);

#endif /* HOSTTEST_USBD_CDC_FLASH_H_ */
