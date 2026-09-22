/*
 * owner_slot.c includes this for Flash_If_Write() alone. The real header
 * (open_plc_cube_ide/Core/Inc/usbd_cdc_flash.h) drags in the HAL and the USB
 * device stack, neither of which exists on a PC, so this declares the one
 * function and nothing else. The prototype must stay identical to the real
 * one or the harness would be testing a different call.
 */

#ifndef HOSTTEST_USBD_CDC_FLASH_H_
#define HOSTTEST_USBD_CDC_FLASH_H_

#include <stdint.h>

uint16_t Flash_If_Write(uint8_t *DataAddress, uint8_t *FlashAddress, uint32_t Len);

#endif /* HOSTTEST_USBD_CDC_FLASH_H_ */
