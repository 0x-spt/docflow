from docflow.imaging import find_sheet, warp


def test_sheet_found_and_straightened(sheet_photo):
    corners = find_sheet(sheet_photo)
    assert corners is not None
    out = warp(sheet_photo, corners)
    assert abs(out.width / out.height - 0.75) < 0.05


def test_no_sheet_returns_none(noise_image):
    assert find_sheet(noise_image) is None
