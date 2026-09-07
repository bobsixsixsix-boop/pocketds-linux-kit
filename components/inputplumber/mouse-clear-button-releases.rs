// SPDX-License-Identifier: GPL-3.0-or-later
// Linux input-event-codes.h: BTN_LEFT through BTN_EXTRA. These are the five
// buttons declared by the pinned InputPlumber MouseDevice, not wheel events.
pub(crate) const BUTTON_CODES: [u16; 5] = [0x110, 0x111, 0x112, 0x113, 0x114];

/// Build only releases for our virtual mouse. Repeating this cannot press a key.
pub(crate) fn release_events() -> [(u16, i32); 5] {
    BUTTON_CODES.map(|code| (code, 0))
}

#[cfg(test)]
mod tests {
    use super::release_events;

    #[test]
    fn releases_every_supported_mouse_button_once() {
        let events = release_events();
        assert_eq!(events.map(|(code, _)| code), [272, 273, 274, 275, 276]);
        assert!(events.iter().all(|(_, value)| *value == 0));
    }

    #[test]
    fn repeated_clear_never_creates_a_press_or_scroll() {
        let mut held = [true; 5];
        for _ in 0..3 {
            for (code, value) in release_events() {
                assert!((272..=276).contains(&code));
                assert_eq!(value, 0);
                held[usize::from(code - 272)] = value != 0;
            }
            assert_eq!(held, [false; 5]);
        }
    }
}
