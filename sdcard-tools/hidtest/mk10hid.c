/*
 * mk10hid - the MK10 half of the call-control test.
 *
 * usbconfig.sh adds a HID function to the MK10's USB gadget with two collections:
 *
 *   report 1  Telephony "Headset": what Teams, Meet, Zoom and Webex look for.
 *             in:  bit 0 Hook Switch (absolute: 1 = off hook), bit 1 Phone Mute (relative: 1 = toggle)
 *             out: bit 0 Off-Hook, bit 1 Mute, bit 2 Ring, bit 3 Hold   (the "lights" a meeting app sets)
 *   report 2  vendor page 0xFF00, 63 bytes each way: our own channel to a host program.
 *
 * This program only relays. Every decision is made on the host:
 *   - an output report that isn't ours (the lights, report 1) goes back to the host as 'O' + length + the
 *     report, so a host program can see exactly what the meeting app said;
 *   - a vendor output report asks for something:
 *       'P'          ping: answered with 'P', version, lights, last telephony input, uptime, counters
 *       'T' b        send telephony input report b (hook switch / mute), answered 'T' ok b
 *       'L'          send the test log back as 'L' seq_hi seq_lo len text... reports, ending with len 0
 *
 *   mk10hid descriptor            print the HID report descriptor (usbconfig.sh writes it into configfs)
 *   mk10hid relay [DEVICE]        relay on DEVICE, default /dev/hidg0
 *   mk10hid relay --fd N          relay on an inherited descriptor (the tests use a socket pair)
 *   --log PATH                    the file 'L' sends back, default /tmp/mk10hid/log.txt
 *
 * Built static, because the MK10's glibc (2.23) is older than any current cross toolchain's: see build.sh.
 */

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define VERSION 1

#define REPORT_TEL 1
#define REPORT_VENDOR 2
#define VENDOR_LEN 63           /* payload bytes, not counting the report id */
#define LOG_TAIL 16384          /* 'L' sends at most this much, from the end of the log */

static const unsigned char descriptor[] = {
    0x05, 0x0B,         /* Usage Page (Telephony) */
    0x09, 0x05,         /* Usage (Headset) */
    0xA1, 0x01,         /* Collection (Application) */
    0x85, REPORT_TEL,   /*   Report ID (1) */
    0x15, 0x00,         /*   Logical Minimum (0) */
    0x25, 0x01,         /*   Logical Maximum (1) */
    0x75, 0x01,         /*   Report Size (1) */
    0x95, 0x01,         /*   Report Count (1) */
    0x09, 0x20,         /*   Usage (Hook Switch) */
    0x81, 0x22,         /*   Input (Data, Variable, Absolute, No Preferred State) */
    0x09, 0x2F,         /*   Usage (Phone Mute) */
    0x81, 0x06,         /*   Input (Data, Variable, Relative): a toggle */
    0x95, 0x06,         /*   Report Count (6) */
    0x81, 0x03,         /*   Input (Constant): padding to a byte */
    0x05, 0x08,         /*   Usage Page (LEDs) */
    0x95, 0x01,         /*   Report Count (1) */
    0x09, 0x17,         /*   Usage (Off-Hook) */
    0x91, 0x22,         /*   Output (Data, Variable, Absolute, No Preferred State) */
    0x09, 0x09,         /*   Usage (Mute) */
    0x91, 0x22,
    0x09, 0x18,         /*   Usage (Ring) */
    0x91, 0x22,
    0x09, 0x20,         /*   Usage (Hold) */
    0x91, 0x22,
    0x95, 0x04,         /*   Report Count (4) */
    0x91, 0x03,         /*   Output (Constant): padding to a byte */
    0xC0,               /* End Collection */

    0x06, 0x00, 0xFF,   /* Usage Page (Vendor 0xFF00) */
    0x09, 0x01,         /* Usage (1) */
    0xA1, 0x01,         /* Collection (Application) */
    0x85, REPORT_VENDOR,/*   Report ID (2) */
    0x15, 0x00,         /*   Logical Minimum (0) */
    0x26, 0xFF, 0x00,   /*   Logical Maximum (255) */
    0x75, 0x08,         /*   Report Size (8) */
    0x95, VENDOR_LEN,   /*   Report Count (63) */
    0x09, 0x01,
    0x81, 0x02,         /*   Input (Data, Variable, Absolute) */
    0x09, 0x01,
    0x91, 0x02,         /*   Output (Data, Variable, Absolute) */
    0xC0,               /* End Collection */
};

static int fd = -1;
static const char *log_path = "/tmp/mk10hid/log.txt";
static unsigned lights, tel_state;
static unsigned n_out, n_in, n_errors, n_dropped;

static double uptime(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

/* To stderr, which usbconfig.sh points at the log file. */
static void say(const char *fmt, ...)
{
    va_list ap;
    fprintf(stderr, "%.1f relay: ", uptime());
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    fflush(stderr);
}

/* One input report, waiting up to a second for the host to take the last one. The gadget holds one report
 * at a time; a Linux host only polls while something has the device open, so never block for ever. */
static int send_report(int id, const unsigned char *data, size_t len, size_t size)
{
    unsigned char buf[1 + VENDOR_LEN] = {0};
    if (size > VENDOR_LEN)
        size = VENDOR_LEN;
    if (len > size)
        len = size;
    buf[0] = (unsigned char)id;
    memcpy(buf + 1, data, len);
    for (int tries = 0; tries < 2; tries++) {
        ssize_t n = write(fd, buf, size + 1);
        if (n == (ssize_t)(size + 1)) {
            n_in++;
            return 0;
        }
        if (n < 0 && errno != EAGAIN && errno != EINTR) {
            n_errors++;
            say("write failed: %s", strerror(errno));
            return -1;
        }
        struct pollfd p = {fd, POLLOUT, 0};
        poll(&p, 1, 1000);
    }
    n_dropped++;
    if (n_dropped <= 5 || n_dropped % 100 == 0)
        say("host isn't reading; dropped an input report (%u so far)", n_dropped);
    return -1;
}

static int send_vendor(const unsigned char *data, size_t len)
{
    return send_report(REPORT_VENDOR, data, len, VENDOR_LEN);
}

static void put32(unsigned char *p, unsigned v)
{
    p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v;
}

static void put16(unsigned char *p, unsigned v)
{
    p[0] = v >> 8; p[1] = v;
}

static void send_ping(void)
{
    unsigned char r[16] = {'P', VERSION, (unsigned char)lights, (unsigned char)tel_state};
    put32(r + 4, (unsigned)uptime());
    put16(r + 8, n_out);
    put16(r + 10, n_in);
    put16(r + 12, n_errors);
    put16(r + 14, n_dropped);
    send_vendor(r, sizeof r);
}

static void send_log(void)
{
    static char text[LOG_TAIL];
    size_t len = 0;
    int lf = open(log_path, O_RDONLY);
    if (lf >= 0) {
        off_t end = lseek(lf, 0, SEEK_END);
        lseek(lf, end > LOG_TAIL ? end - LOG_TAIL : 0, SEEK_SET);
        ssize_t n;
        while (len < sizeof text && (n = read(lf, text + len, sizeof text - len)) > 0)
            len += n;
        close(lf);
    } else {
        len = snprintf(text, sizeof text, "(no log at %s: %s)\n", log_path, strerror(errno));
    }
    unsigned seq = 0;
    for (size_t at = 0;; seq++) {
        unsigned char r[VENDOR_LEN] = {'L'};
        size_t take = len - at > VENDOR_LEN - 4 ? VENDOR_LEN - 4 : len - at;
        put16(r + 1, seq);
        r[3] = (unsigned char)take;
        memcpy(r + 4, text + at, take);
        if (send_vendor(r, 4 + take) < 0)
            return;
        if (take == 0)
            return;
        at += take;
    }
}

static void vendor_command(const unsigned char *cmd, size_t len)
{
    switch (len ? cmd[0] : 0) {
    case 'P':
        send_ping();
        break;
    case 'T': {
        unsigned char b = len > 1 ? cmd[1] : 0;
        int ok = send_report(REPORT_TEL, &b, 1, 1) == 0;
        if (ok)
            tel_state = b;
        say("host asked for telephony input 0x%02x: %s", b, ok ? "sent" : "NOT sent");
        unsigned char r[3] = {'T', (unsigned char)ok, b};
        send_vendor(r, sizeof r);
        break;
    }
    case 'L':
        send_log();
        break;
    default: {
        unsigned char r[2] = {'?', len ? cmd[0] : 0};
        send_vendor(r, sizeof r);
    }
    }
}

static void output_report(const unsigned char *buf, size_t n)
{
    n_out++;
    if (n >= 1 && buf[0] == REPORT_VENDOR) {
        vendor_command(buf + 1, n - 1);
        return;
    }
    if (n >= 2 && buf[0] == REPORT_TEL) {
        if (buf[1] != lights)
            say("lights now 0x%02x (off-hook %d, mute %d, ring %d, hold %d)", buf[1],
                buf[1] & 1, (buf[1] >> 1) & 1, (buf[1] >> 2) & 1, (buf[1] >> 3) & 1);
        lights = buf[1];
    } else {
        say("output report of %zu bytes, first 0x%02x", n, n ? buf[0] : 0);
    }
    unsigned char r[VENDOR_LEN] = {'O', (unsigned char)(n > VENDOR_LEN - 2 ? VENDOR_LEN - 2 : n)};
    memcpy(r + 2, buf, r[1]);
    send_vendor(r, 2 + r[1]);
}

static int relay(void)
{
    int flags = fcntl(fd, F_GETFL);
    fcntl(fd, F_SETFL, flags | O_NONBLOCK);
    say("version %d relaying, log %s", VERSION, log_path);
    unsigned failures = 0;
    for (;;) {
        struct pollfd p = {fd, POLLIN, 0};
        if (poll(&p, 1, -1) < 0) {
            if (errno == EINTR)
                continue;
            say("poll failed: %s", strerror(errno));
            return 1;
        }
        unsigned char buf[1024];
        ssize_t n = read(fd, buf, sizeof buf);
        if (n > 0) {
            failures = 0;
            output_report(buf, (size_t)n);
            continue;
        }
        if (n < 0 && (errno == EAGAIN || errno == EINTR))
            continue;
        /* 0 is the other end going away (tests); an error is the gadget being unbound. Either way stop, and
         * let whoever started us decide whether to start again. */
        if (n == 0 || ++failures > 20) {
            say("stopping: %s", n == 0 ? "end of file" : strerror(errno));
            return n == 0 ? 0 : 1;
        }
        n_errors++;
        say("read failed: %s", strerror(errno));
        sleep(1);
    }
}

int main(int argc, char **argv)
{
    if (argc >= 2 && strcmp(argv[1], "descriptor") == 0) {
        /* One write: configfs takes report_desc from a single write() call. */
        return write(1, descriptor, sizeof descriptor) == sizeof descriptor ? 0 : 1;
    }
    if (argc >= 2 && strcmp(argv[1], "relay") == 0) {
        const char *dev = "/dev/hidg0";
        for (int i = 2; i < argc; i++) {
            if (strcmp(argv[i], "--fd") == 0 && i + 1 < argc)
                fd = atoi(argv[++i]);
            else if (strcmp(argv[i], "--log") == 0 && i + 1 < argc)
                log_path = argv[++i];
            else
                dev = argv[i];
        }
        if (fd < 0 && (fd = open(dev, O_RDWR)) < 0) {
            say("can't open %s: %s", dev, strerror(errno));
            return 1;
        }
        return relay();
    }
    fprintf(stderr, "usage: %s descriptor | relay [DEVICE | --fd N] [--log PATH]\n", argv[0]);
    return 2;
}
