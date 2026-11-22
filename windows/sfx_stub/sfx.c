/*
 * OpenRAR self-extracting archive module.
 *
 * This executable is prepended to a ZIP archive. At start-up it locates the
 * archive appended to its own file, reads the SFX script stored in the ZIP
 * comment and extracts the files (Store / Deflate methods).
 *
 * Script keys (one per line between ";OpenRAR-SFX-begin" and ";OpenRAR-SFX-end"):
 *   Title=     window title
 *   Text=      message shown in the dialog ("\n" for new lines)
 *   Path=      default destination, environment variables are expanded
 *   Setup=     program (relative to destination) to run after extraction
 *   Silent=1   no user interface
 *   Overwrite= 0 ask, 1 overwrite all, 2 skip existing
 *   TempMode=1 extract to a temporary folder, run Setup, wait, then clean up
 *   License=   text the user must accept before extracting
 *
 * Command line: /S silent, /D=<folder> destination, /? help.
 *
 * Build: x86_64-w64-mingw32-gcc -O2 -municode -mwindows sfx.c sfx.res.o -lz -lcomctl32 -lshell32 -lole32
 */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <commctrl.h>
#include <shlobj.h>
#include <shellapi.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include <zlib.h>

#define WM_SFX_PROGRESS (WM_APP + 1)
#define WM_SFX_DONE     (WM_APP + 2)
#define IDC_DEST   101
#define IDC_BROWSE 102
#define IDC_PROG   103
#define IDC_OK     IDOK
#define IDC_CANCEL IDCANCEL
#define IDC_STATUS 104
#define IDC_TEXT   105
#define IDC_LICENSE 106

typedef struct {
    wchar_t *name;
    uint16_t flags, method, mtime, mdate;
    uint32_t crc;
    uint64_t csize, usize, offset;
    uint32_t attr;
} ZEntry;

static HANDLE g_file = INVALID_HANDLE_VALUE;
static ZEntry *g_entries = NULL;
static size_t g_count = 0;
static uint64_t g_total = 0;
static wchar_t g_title[256] = L"OpenRAR self-extracting archive";
static wchar_t *g_text = NULL, *g_setup = NULL, *g_license = NULL;
static wchar_t g_dest[MAX_PATH * 2] = L"";
static int g_silent = 0, g_overwrite = 0, g_tempmode = 0, g_dest_given = 0;
static volatile LONG g_cancel = 0;
static int g_ow_decided = -1;             /* -1 ask, 1 overwrite all, 0 skip all */
static HWND g_dlg = NULL;
static HFONT g_font = NULL;
static wchar_t g_error[1024] = L"";

/* ------------------------------------------------------------------ helpers */
static wchar_t *tok(wchar_t *s, const wchar_t *delim, wchar_t **ctx) {
    if (!s) s = *ctx;
    if (!s) return NULL;
    while (*s && wcschr(delim, *s)) s++;
    if (!*s) { *ctx = NULL; return NULL; }
    wchar_t *e = s;
    while (*e && !wcschr(delim, *e)) e++;
    if (*e) { *e = 0; *ctx = e + 1; } else *ctx = NULL;
    return s;
}
static int read_at(uint64_t off, void *buf, DWORD len) {
    LARGE_INTEGER li; li.QuadPart = (LONGLONG)off;
    if (!SetFilePointerEx(g_file, li, NULL, FILE_BEGIN)) return 0;
    DWORD got = 0;
    return ReadFile(g_file, buf, len, &got, NULL) && got == len;
}
static uint16_t le16(const uint8_t *p) { return (uint16_t)(p[0] | (p[1] << 8)); }
static uint32_t le32(const uint8_t *p) { return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24); }
static uint64_t le64(const uint8_t *p) { return (uint64_t)le32(p) | ((uint64_t)le32(p + 4) << 32); }

static wchar_t *to_wide(const char *s, int len, UINT cp) {
    int n = MultiByteToWideChar(cp, 0, s, len, NULL, 0);
    wchar_t *w = (wchar_t *)calloc((size_t)n + 1, sizeof(wchar_t));
    MultiByteToWideChar(cp, 0, s, len, w, n);
    w[n] = 0;
    return w;
}

static void fail(const wchar_t *fmt, const wchar_t *arg) {
    _snwprintf(g_error, 1023, fmt, arg ? arg : L"");
}

/* ------------------------------------------------------------------ zip parsing */
static wchar_t *unescape(wchar_t *v) {
    wchar_t *r = v, *w = v;
    while (*r) {
        if (r[0] == L'\\' && r[1] == L'n') { *w++ = L'\r'; *w++ = L'\n'; r += 2; }
        else *w++ = *r++;
    }
    *w = 0;
    /* note: '\r\n' expansion may grow the string; buffer was over-allocated */
    return v;
}

static void parse_script(const char *comment, int len) {
    wchar_t *all = to_wide(comment, len, CP_UTF8);
    wchar_t *start = wcsstr(all, L";OpenRAR-SFX-begin");
    if (!start) { free(all); return; }
    wchar_t *end = wcsstr(start, L";OpenRAR-SFX-end");
    if (end) *end = 0;
    wchar_t *ctx = NULL;
    for (wchar_t *line = tok(start, L"\r\n", &ctx); line; line = tok(NULL, L"\r\n", &ctx)) {
        wchar_t *eq = wcschr(line, L'=');
        if (!eq || line[0] == L';') continue;
        *eq = 0;
        wchar_t *key = line, *val = eq + 1;
        size_t vl = wcslen(val);
        wchar_t *copy = (wchar_t *)calloc(vl * 2 + 2, sizeof(wchar_t));
        wcscpy(copy, val);
        unescape(copy);
        if (!_wcsicmp(key, L"Title")) { wcsncpy(g_title, copy, 255); free(copy); }
        else if (!_wcsicmp(key, L"Text")) g_text = copy;
        else if (!_wcsicmp(key, L"License")) g_license = copy;
        else if (!_wcsicmp(key, L"Setup")) g_setup = copy;
        else if (!_wcsicmp(key, L"Path")) {
            if (!g_dest_given) ExpandEnvironmentStringsW(copy, g_dest, MAX_PATH * 2);
            free(copy);
        }
        else if (!_wcsicmp(key, L"Silent")) { g_silent |= _wtoi(copy) != 0; free(copy); }
        else if (!_wcsicmp(key, L"Overwrite")) { g_overwrite = _wtoi(copy); free(copy); }
        else if (!_wcsicmp(key, L"TempMode")) { g_tempmode = _wtoi(copy) != 0; free(copy); }
        else free(copy);
    }
    free(all);
}

static int open_archive(void) {
    wchar_t self[MAX_PATH * 2];
    GetModuleFileNameW(NULL, self, MAX_PATH * 2);
    g_file = CreateFileW(self, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (g_file == INVALID_HANDLE_VALUE) { fail(L"Cannot open %s", self); return 0; }
    LARGE_INTEGER sz; GetFileSizeEx(g_file, &sz);
    uint64_t size = (uint64_t)sz.QuadPart;
    DWORD tail_len = (DWORD)(size < 65557 ? size : 65557);
    uint8_t *tail = (uint8_t *)malloc(tail_len);
    if (!read_at(size - tail_len, tail, tail_len)) { fail(L"Cannot read archive%s", NULL); return 0; }
    int pos = -1;
    for (int i = (int)tail_len - 22; i >= 0; i--)
        if (tail[i] == 'P' && tail[i + 1] == 'K' && tail[i + 2] == 5 && tail[i + 3] == 6) { pos = i; break; }
    if (pos < 0) { fail(L"No archive data was found in this file.%s", NULL); return 0; }
    uint64_t eocd = size - tail_len + (uint64_t)pos;
    uint64_t count = le16(tail + pos + 10);
    uint64_t cd_size = le32(tail + pos + 12);
    uint64_t cd_off = le32(tail + pos + 16);
    uint16_t clen = le16(tail + pos + 20);
    if ((uint64_t)pos + 22 + clen <= tail_len) parse_script((const char *)tail + pos + 22, clen);
    uint64_t cd_abs = eocd - cd_size;
    if (count == 0xFFFF || cd_size == 0xFFFFFFFFu || cd_off == 0xFFFFFFFFu) {
        uint8_t loc[20], rec[56];
        if (read_at(eocd - 20, loc, 20) && le32(loc) == 0x07064b50) {
            uint64_t e64 = eocd - 20 - 56;
            if (read_at(e64, rec, 56) && le32(rec) == 0x06064b50) {
                count = le64(rec + 32); cd_size = le64(rec + 40); cd_off = le64(rec + 48);
                cd_abs = e64 - cd_size;
            }
        }
    }
    free(tail);
    uint64_t concat = cd_abs - cd_off;
    uint8_t *cd = (uint8_t *)malloc((size_t)cd_size + 1);
    if (!read_at(cd_abs, cd, (DWORD)cd_size)) { fail(L"Archive directory is damaged.%s", NULL); return 0; }
    g_entries = (ZEntry *)calloc((size_t)count + 1, sizeof(ZEntry));
    size_t i = 0;
    uint8_t *p = cd, *endp = cd + cd_size;
    while (p + 46 <= endp && le32(p) == 0x02014b50 && i < count) {
        ZEntry *e = &g_entries[i];
        e->flags = le16(p + 8); e->method = le16(p + 10);
        e->mtime = le16(p + 12); e->mdate = le16(p + 14);
        e->crc = le32(p + 16); e->csize = le32(p + 20); e->usize = le32(p + 24);
        uint16_t nl = le16(p + 28), el = le16(p + 30), cl = le16(p + 32);
        e->attr = le32(p + 38);
        e->offset = le32(p + 42);
        uint8_t *x = p + 46 + nl, *xe = x + el;
        while (x + 4 <= xe) {
            uint16_t id = le16(x), ln = le16(x + 2);
            if (id == 1) {
                uint8_t *q = x + 4;
                if (e->usize == 0xFFFFFFFFu) { e->usize = le64(q); q += 8; }
                if (e->csize == 0xFFFFFFFFu) { e->csize = le64(q); q += 8; }
                if (e->offset == 0xFFFFFFFFu) { e->offset = le64(q); }
            }
            x += 4 + ln;
        }
        e->offset += concat;
        e->name = to_wide((const char *)p + 46, nl, (e->flags & 0x800) ? CP_UTF8 : 437);
        if (!(e->name[0] && e->name[wcslen(e->name) - 1] == L'/')) g_total += e->usize;
        p += 46 + nl + el + cl;
        i++;
    }
    g_count = i;
    free(cd);
    return 1;
}

/* ------------------------------------------------------------------ extraction */
static int make_dirs(const wchar_t *path) {
    wchar_t tmp[MAX_PATH * 2];
    wcsncpy(tmp, path, MAX_PATH * 2 - 1);
    tmp[MAX_PATH * 2 - 1] = 0;
    for (wchar_t *p = tmp + 3; *p; p++) {
        if (*p == L'\\') { *p = 0; CreateDirectoryW(tmp, NULL); *p = L'\\'; }
    }
    CreateDirectoryW(tmp, NULL);
    return 1;
}

/* sanitize an archive name into a safe relative Windows path */
static void safe_rel(const wchar_t *name, wchar_t *out, size_t cap) {
    out[0] = 0;
    wchar_t buf[MAX_PATH * 2];
    wcsncpy(buf, name, MAX_PATH * 2 - 1); buf[MAX_PATH * 2 - 1] = 0;
    for (wchar_t *c = buf; *c; c++) if (*c == L'/') *c = L'\\';
    wchar_t *ctx = NULL;
    for (wchar_t *part = tok(buf, L"\\", &ctx); part; part = tok(NULL, L"\\", &ctx)) {
        if (!wcscmp(part, L".") || !wcscmp(part, L"..") || wcschr(part, L':')) continue;
        for (wchar_t *c = part; *c; c++) if (wcschr(L"<>\"|?*", *c) || *c < 32) *c = L'_';
        if (out[0]) wcsncat(out, L"\\", cap - wcslen(out) - 1);
        wcsncat(out, part, cap - wcslen(out) - 1);
    }
}

static uint64_t g_done = 0;

static void post_progress(void) {
    if (g_dlg) PostMessageW(g_dlg, WM_SFX_PROGRESS, 0, 0);
}

static int ask_overwrite(const wchar_t *path) {
    if (g_overwrite == 1) return 1;
    if (g_overwrite == 2) return 0;
    if (g_ow_decided >= 0) return g_ow_decided;
    if (g_silent) return 1;
    wchar_t msg[MAX_PATH * 3];
    _snwprintf(msg, MAX_PATH * 3 - 1,
               L"The following file already exists:\n%s\n\nReplace existing files?\n\n"
               L"Yes = replace all, No = skip existing files, Cancel = stop", path);
    int r = MessageBoxW(g_dlg, msg, g_title, MB_YESNOCANCEL | MB_ICONQUESTION);
    if (r == IDCANCEL) { InterlockedExchange(&g_cancel, 1); return 0; }
    g_ow_decided = (r == IDYES);
    return g_ow_decided;
}

static int extract_entry(ZEntry *e) {
    wchar_t rel[MAX_PATH * 2], full[MAX_PATH * 2];
    safe_rel(e->name, rel, MAX_PATH * 2);
    if (!rel[0]) return 1;
    _snwprintf(full, MAX_PATH * 2 - 1, L"%s\\%s", g_dest, rel);
    full[MAX_PATH * 2 - 1] = 0;
    size_t nl = wcslen(e->name);
    if (nl && e->name[nl - 1] == L'/') { make_dirs(full); return 1; }
    if (e->flags & 1) { fail(L"%s is encrypted. Encrypted files are not supported by the SFX module.", e->name); return 0; }
    if (e->method != 0 && e->method != 8) { fail(L"%s uses an unsupported compression method.", e->name); return 0; }

    uint8_t lh[30];
    if (!read_at(e->offset, lh, 30) || le32(lh) != 0x04034b50) { fail(L"Data of %s is damaged.", e->name); return 0; }
    uint64_t data = e->offset + 30 + le16(lh + 26) + le16(lh + 28);

    wchar_t dir[MAX_PATH * 2];
    wcscpy(dir, full);
    wchar_t *slash = wcsrchr(dir, L'\\');
    if (slash) { *slash = 0; make_dirs(dir); }

    if (GetFileAttributesW(full) != INVALID_FILE_ATTRIBUTES) {
        if (!ask_overwrite(full)) { g_done += e->usize; post_progress(); return !g_cancel; }
        SetFileAttributesW(full, FILE_ATTRIBUTE_NORMAL);
    }
    HANDLE out = CreateFileW(full, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (out == INVALID_HANDLE_VALUE) { fail(L"Cannot create %s", full); return 0; }

    static uint8_t inbuf[1 << 16], outbuf[1 << 17];
    uint64_t left = e->csize, pos = data;
    uint32_t crc = crc32(0L, Z_NULL, 0);
    int ok = 1;
    z_stream zs; memset(&zs, 0, sizeof(zs));
    if (e->method == 8 && inflateInit2(&zs, -MAX_WBITS) != Z_OK) ok = 0;
    int zdone = 0;
    while (ok && left > 0 && !zdone) {
        if (g_cancel) { ok = 0; break; }
        DWORD n = (DWORD)(left < sizeof(inbuf) ? left : sizeof(inbuf));
        if (!read_at(pos, inbuf, n)) { ok = 0; break; }
        pos += n; left -= n;
        DWORD wr;
        if (e->method == 0) {
            crc = crc32(crc, inbuf, n);
            WriteFile(out, inbuf, n, &wr, NULL);
            g_done += n;
        } else {
            zs.next_in = inbuf; zs.avail_in = n;
            while (zs.avail_in > 0 || zs.avail_out == 0) {
                zs.next_out = outbuf; zs.avail_out = sizeof(outbuf);
                int r = inflate(&zs, Z_NO_FLUSH);
                if (r != Z_OK && r != Z_STREAM_END && r != Z_BUF_ERROR) { ok = 0; break; }
                DWORD got = (DWORD)(sizeof(outbuf) - zs.avail_out);
                if (got) { crc = crc32(crc, outbuf, got); WriteFile(out, outbuf, got, &wr, NULL); g_done += got; }
                if (r == Z_STREAM_END) { zdone = 1; break; }
                if (r == Z_BUF_ERROR && got == 0) break;
            }
        }
        post_progress();
    }
    if (e->method == 8) inflateEnd(&zs);
    FILETIME lft, ft;
    if (DosDateTimeToFileTime(e->mdate, e->mtime, &lft) && LocalFileTimeToFileTime(&lft, &ft))
        SetFileTime(out, NULL, NULL, &ft);
    CloseHandle(out);
    if (ok && crc != e->crc) { fail(L"CRC failed in %s. The file is corrupt.", e->name); ok = 0; }
    if (!ok) {
        DeleteFileW(full);
        if (!g_error[0] && !g_cancel) fail(L"Error while extracting %s", e->name);
        return 0;
    }
    if (e->attr & 0x01) SetFileAttributesW(full, FILE_ATTRIBUTE_READONLY);
    return 1;
}

static DWORD WINAPI extract_thread(LPVOID arg) {
    (void)arg;
    int ok = 1;
    make_dirs(g_dest);
    for (size_t i = 0; i < g_count && ok; i++) {
        if (g_cancel) { ok = 0; break; }
        if (g_dlg) SendMessageW(GetDlgItem(g_dlg, IDC_STATUS), WM_SETTEXT, 0, (LPARAM)g_entries[i].name);
        ok = extract_entry(&g_entries[i]);
    }
    if (g_dlg) PostMessageW(g_dlg, WM_SFX_DONE, ok, 0);
    return ok ? 0 : 1;
}

static int run_setup(void) {
    if (!g_setup || !g_setup[0]) return 0;
    wchar_t prog[MAX_PATH * 2] = L"", *params = NULL;
    wchar_t *s = g_setup;
    while (*s == L' ') s++;
    if (*s == L'"') {
        wchar_t *q = wcschr(s + 1, L'"');
        size_t n = q ? (size_t)(q - s - 1) : wcslen(s + 1);
        wcsncpy(prog, s + 1, n); prog[n] = 0;
        params = q ? q + 1 : NULL;
    } else {
        wchar_t *sp = wcschr(s, L' ');
        size_t n = sp ? (size_t)(sp - s) : wcslen(s);
        wcsncpy(prog, s, n); prog[n] = 0;
        params = sp ? sp + 1 : NULL;
    }
    wchar_t full[MAX_PATH * 3];
    if (wcschr(prog, L':')) wcscpy(full, prog);
    else _snwprintf(full, MAX_PATH * 3 - 1, L"%s\\%s", g_dest, prog);
    SHELLEXECUTEINFOW sei; memset(&sei, 0, sizeof(sei));
    sei.cbSize = sizeof(sei);
    sei.fMask = SEE_MASK_NOCLOSEPROCESS;
    sei.lpFile = full;
    sei.lpParameters = params;
    sei.lpDirectory = g_dest;
    sei.nShow = SW_SHOWNORMAL;
    if (!ShellExecuteExW(&sei)) {
        if (!g_silent) MessageBoxW(NULL, L"Could not start the setup program.", g_title, MB_ICONERROR);
        return 1;
    }
    if (g_tempmode && sei.hProcess) WaitForSingleObject(sei.hProcess, INFINITE);
    if (sei.hProcess) CloseHandle(sei.hProcess);
    return 0;
}

static void remove_tree(const wchar_t *path) {
    wchar_t from[MAX_PATH * 2 + 2];
    memset(from, 0, sizeof(from));
    wcsncpy(from, path, MAX_PATH * 2);
    SHFILEOPSTRUCTW op; memset(&op, 0, sizeof(op));
    op.wFunc = FO_DELETE; op.pFrom = from;
    op.fFlags = FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT;
    SHFileOperationW(&op);
}

/* ------------------------------------------------------------------ UI */
static HWND add_ctrl(HWND parent, const wchar_t *cls, const wchar_t *text, DWORD style, int x, int y, int w, int h, int id, DWORD ex) {
    HWND c = CreateWindowExW(ex, cls, text, WS_CHILD | WS_VISIBLE | style, x, y, w, h, parent, (HMENU)(INT_PTR)id, GetModuleHandleW(NULL), NULL);
    SendMessageW(c, WM_SETFONT, (WPARAM)g_font, TRUE);
    return c;
}

static int g_license_ok = 0;

static LRESULT CALLBACK license_proc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_COMMAND:
        if (LOWORD(w) == IDOK) { g_license_ok = 1; DestroyWindow(h); }
        else if (LOWORD(w) == IDCANCEL) { DestroyWindow(h); }
        return 0;
    case WM_CLOSE: DestroyWindow(h); return 0;
    case WM_DESTROY: PostQuitMessage(0); return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

static int show_license(void) {
    WNDCLASSW wc; memset(&wc, 0, sizeof(wc));
    wc.lpfnWndProc = license_proc; wc.hInstance = GetModuleHandleW(NULL);
    wc.lpszClassName = L"OpenRARSfxLicense"; wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
    wc.hIcon = LoadIconW(GetModuleHandleW(NULL), MAKEINTRESOURCEW(1));
    RegisterClassW(&wc);
    HWND h = CreateWindowExW(WS_EX_DLGMODALFRAME, wc.lpszClassName, L"License agreement",
                             WS_CAPTION | WS_SYSMENU | WS_VISIBLE, CW_USEDEFAULT, CW_USEDEFAULT, 520, 420, NULL, NULL, wc.hInstance, NULL);
    add_ctrl(h, L"STATIC", L"Please read the following license agreement carefully:", 0, 12, 10, 480, 20, -1, 0);
    add_ctrl(h, L"EDIT", g_license, ES_MULTILINE | ES_READONLY | WS_VSCROLL | ES_AUTOVSCROLL, 12, 34, 480, 290, IDC_LICENSE, WS_EX_CLIENTEDGE);
    add_ctrl(h, L"BUTTON", L"I &accept", BS_DEFPUSHBUTTON | WS_TABSTOP, 290, 336, 96, 28, IDOK, 0);
    add_ctrl(h, L"BUTTON", L"I &decline", WS_TABSTOP, 396, 336, 96, 28, IDCANCEL, 0);
    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0) > 0) {
        if (!IsDialogMessageW(h, &msg)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
    }
    return g_license_ok;
}

static int g_result = 2;
static HANDLE g_thread = NULL;

static void browse(HWND h) {
    BROWSEINFOW bi; memset(&bi, 0, sizeof(bi));
    bi.hwndOwner = h; bi.lpszTitle = L"Select destination folder";
    bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE;
    LPITEMIDLIST pidl = SHBrowseForFolderW(&bi);
    if (pidl) {
        wchar_t path[MAX_PATH];
        if (SHGetPathFromIDListW(pidl, path)) SetDlgItemTextW(h, IDC_DEST, path);
        CoTaskMemFree(pidl);
    }
}

static LRESULT CALLBACK main_proc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_COMMAND:
        switch (LOWORD(w)) {
        case IDC_BROWSE: browse(h); return 0;
        case IDC_OK:
            GetDlgItemTextW(h, IDC_DEST, g_dest, MAX_PATH * 2);
            if (!g_dest[0]) return 0;
            EnableWindow(GetDlgItem(h, IDC_OK), FALSE);
            EnableWindow(GetDlgItem(h, IDC_BROWSE), FALSE);
            EnableWindow(GetDlgItem(h, IDC_DEST), FALSE);
            g_thread = CreateThread(NULL, 0, extract_thread, NULL, 0, NULL);
            return 0;
        case IDC_CANCEL:
            if (g_thread) { InterlockedExchange(&g_cancel, 1); return 0; }
            g_result = 2; DestroyWindow(h); return 0;
        }
        break;
    case WM_SFX_PROGRESS: {
        int pct = g_total ? (int)(g_done * 1000 / g_total) : 1000;
        SendMessageW(GetDlgItem(h, IDC_PROG), PBM_SETPOS, pct, 0);
        return 0;
    }
    case WM_SFX_DONE:
        if (g_thread) { WaitForSingleObject(g_thread, INFINITE); CloseHandle(g_thread); g_thread = NULL; }
        if (w) { g_result = 0; }
        else if (g_cancel) { g_result = 2; }
        else { MessageBoxW(h, g_error[0] ? g_error : L"Extraction failed.", g_title, MB_ICONERROR); g_result = 1; }
        DestroyWindow(h);
        return 0;
    case WM_CLOSE:
        if (g_thread) { InterlockedExchange(&g_cancel, 1); return 0; }
        DestroyWindow(h); return 0;
    case WM_DESTROY: PostQuitMessage(0); return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

static int run_dialog(void) {
    WNDCLASSW wc; memset(&wc, 0, sizeof(wc));
    wc.lpfnWndProc = main_proc; wc.hInstance = GetModuleHandleW(NULL);
    wc.lpszClassName = L"OpenRARSfx"; wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
    wc.hIcon = LoadIconW(GetModuleHandleW(NULL), MAKEINTRESOURCEW(1));
    RegisterClassW(&wc);
    int text_h = g_text ? 96 : 0;
    int H = 200 + text_h;
    RECT r = {0, 0, 480, H};
    AdjustWindowRect(&r, WS_CAPTION | WS_SYSMENU, FALSE);
    int ww = r.right - r.left, wh = r.bottom - r.top;
    int sx = (GetSystemMetrics(SM_CXSCREEN) - ww) / 2, sy = (GetSystemMetrics(SM_CYSCREEN) - wh) / 2;
    g_dlg = CreateWindowExW(WS_EX_DLGMODALFRAME, wc.lpszClassName, g_title, WS_CAPTION | WS_SYSMENU | WS_VISIBLE,
                            sx, sy, ww, wh, NULL, NULL, wc.hInstance, NULL);
    int y = 14;
    if (g_text) {
        add_ctrl(g_dlg, L"EDIT", g_text, ES_MULTILINE | ES_READONLY | WS_VSCROLL, 14, y, 452, text_h - 10, IDC_TEXT, WS_EX_CLIENTEDGE);
        y += text_h;
    }
    add_ctrl(g_dlg, L"STATIC", L"Destination folder:", 0, 14, y, 300, 18, -1, 0); y += 22;
    add_ctrl(g_dlg, L"EDIT", g_dest, ES_AUTOHSCROLL | WS_TABSTOP, 14, y, 360, 24, IDC_DEST, WS_EX_CLIENTEDGE);
    add_ctrl(g_dlg, L"BUTTON", L"&Browse...", WS_TABSTOP, 382, y - 1, 84, 26, IDC_BROWSE, 0); y += 36;
    HWND pb = add_ctrl(g_dlg, PROGRESS_CLASSW, L"", 0, 14, y, 452, 18, IDC_PROG, 0); y += 24;
    SendMessageW(pb, PBM_SETRANGE32, 0, 1000);
    add_ctrl(g_dlg, L"STATIC", L"", SS_PATHELLIPSIS, 14, y, 452, 18, IDC_STATUS, 0); y += 30;
    add_ctrl(g_dlg, L"BUTTON", L"E&xtract", BS_DEFPUSHBUTTON | WS_TABSTOP, 270, y, 96, 28, IDC_OK, 0);
    add_ctrl(g_dlg, L"BUTTON", L"Cancel", WS_TABSTOP, 370, y, 96, 28, IDC_CANCEL, 0);
    SetFocus(GetDlgItem(g_dlg, IDC_OK));
    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0) > 0) {
        if (!IsDialogMessageW(g_dlg, &msg)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
    }
    g_dlg = NULL;
    return g_result;
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR cmd, int show) {
    (void)inst; (void)prev; (void)show; (void)cmd;
    INITCOMMONCONTROLSEX icc = {sizeof(icc), ICC_PROGRESS_CLASS | ICC_STANDARD_CLASSES};
    InitCommonControlsEx(&icc);
    CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    NONCLIENTMETRICSW ncm; ncm.cbSize = sizeof(ncm);
    SystemParametersInfoW(SPI_GETNONCLIENTMETRICS, sizeof(ncm), &ncm, 0);
    g_font = CreateFontIndirectW(&ncm.lfMessageFont);

    int argc = 0;
    LPWSTR *argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    for (int i = 1; i < argc; i++) {
        if (!_wcsicmp(argv[i], L"/S") || !_wcsicmp(argv[i], L"-s")) g_silent = 1;
        else if (!_wcsnicmp(argv[i], L"/D=", 3)) { ExpandEnvironmentStringsW(argv[i] + 3, g_dest, MAX_PATH * 2); g_dest_given = 1; }
        else if (!wcscmp(argv[i], L"/?")) {
            MessageBoxW(NULL, L"Options:\n  /S\t\tsilent mode\n  /D=<folder>\tdestination folder", L"OpenRAR SFX", MB_ICONINFORMATION);
            return 0;
        }
    }
    if (!open_archive()) { if (!g_silent) MessageBoxW(NULL, g_error, g_title, MB_ICONERROR); return 1; }

    if (g_tempmode) {
        wchar_t tmp[MAX_PATH];
        GetTempPathW(MAX_PATH, tmp);
        _snwprintf(g_dest, MAX_PATH * 2 - 1, L"%sOpenRARSfx%lu", tmp, (unsigned long)GetTickCount());
    } else if (!g_dest[0]) {
        wchar_t self[MAX_PATH * 2];
        GetModuleFileNameW(NULL, self, MAX_PATH * 2);
        wcscpy(g_dest, self);
        wchar_t *dot = wcsrchr(g_dest, L'.');
        if (dot && dot > wcsrchr(g_dest, L'\\')) *dot = 0;
    }
    if (g_license && !g_silent && !show_license()) return 2;

    int rc;
    if (g_silent || g_tempmode) {
        rc = (int)extract_thread(NULL);
        if (rc && !g_silent) MessageBoxW(NULL, g_error[0] ? g_error : L"Extraction failed.", g_title, MB_ICONERROR);
    } else {
        rc = run_dialog();
    }
    if (rc == 0) run_setup();
    if (g_tempmode) remove_tree(g_dest);
    CloseHandle(g_file);
    return rc;
}
