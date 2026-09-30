/*
 * The sector-15 reclaim, reduced to what owner_slot.c relies on: the root
 * area comes back erased except for what the carry holds. The real one
 * (bootloader_state.c) also moves calibration and metadata and stages the
 * carry in backup SRAM; T2-34 (host/sector15_reclaim) covers that part.
 */

#include "owner_slot.h"
#include "bootloader_state.h"
#include "fake_owner_flash.h"
#include <string.h>

uint32_t fake_reclaim_count;

bool bootloader_state_reclaim(const owner_carry_t *carry)
{
	bool ok;

	memset(fake_owner_area, 0xFF, sizeof(fake_owner_area));
	fake_reclaim_count++;
	ok = owner_slot_write_carry(carry);
	owner_slot_rescan();
	return ok;
}
