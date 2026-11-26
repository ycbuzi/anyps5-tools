#include <sys/types.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <fcntl.h>
#include <poll.h>
#include <unistd.h>
#include "probe.h"

static int optval(int fd, int level, int name) {
    int v = -1;
    socklen_t len = sizeof v;
    if (getsockopt(fd, level, name, &v, &len) != 0) return -1000 - errno;
    return v;
}

static struct sockaddr_in loopback(unsigned short port) {
    struct sockaddr_in a;
    memset(&a, 0, sizeof a);
    a.sin_len = sizeof a;
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    return a;
}

int main(void) {
    probe_name = "socket_ops";
    signal(SIGPIPE, SIG_IGN);
    char buf[256];

    CHECK("socket_bad_domain", socket(9999, SOCK_STREAM, 0));
    CHECK("socket_bad_type", socket(AF_INET, 99, 0));
    CHECK("socket_udp_with_tcp_proto", socket(AF_INET, SOCK_DGRAM, IPPROTO_TCP));

    int sv[2];
    CHECK("socketpair_stream", socketpair(AF_UNIX, SOCK_STREAM, 0, sv));
    CHECK("send_zero", send(sv[0], buf, 0, 0));
    CHECK("recv_dontwait_empty", recv(sv[1], buf, sizeof buf, MSG_DONTWAIT));
    send(sv[0], "abcdef", 6, 0);
    CHECK("recv_peek", recv(sv[1], buf, 3, MSG_PEEK));
    CHECK("recv_after_peek", recv(sv[1], buf, sizeof buf, 0));
    CHECK("shutdown_wr", shutdown(sv[0], SHUT_WR));
    CHECK("recv_after_peer_shutdown", recv(sv[1], buf, sizeof buf, 0));
    CHECK("send_after_own_shutdown", send(sv[0], "x", 1, 0));
    CHECK("shutdown_bad_how", shutdown(sv[0], 9));
    close(sv[1]);
    CHECK("send_peer_closed", send(sv[0], "x", 1, 0));
    close(sv[0]);

    CHECK("socketpair_dgram", socketpair(AF_UNIX, SOCK_DGRAM, 0, sv));
    send(sv[0], "0123456789", 10, 0);
    CHECK("dgram_recv_truncated", recv(sv[1], buf, 4, 0));
    CHECK("dgram_rest_discarded", recv(sv[1], buf, sizeof buf, MSG_DONTWAIT));
    send(sv[0], "0123456789", 10, 0);
    CHECK("dgram_recv_trunc_flag_len", recv(sv[1], buf, 4, MSG_TRUNC));
    close(sv[0]);
    close(sv[1]);
    CHECK("socketpair_inet", socketpair(AF_INET, SOCK_STREAM, 0, sv));

    int t = socket(AF_INET, SOCK_STREAM, 0);
    NOTE("tcp_default_sndbuf_positive", "%d", optval(t, SOL_SOCKET, SO_SNDBUF) > 0);
    NOTE("tcp_default_nodelay", "%d", optval(t, IPPROTO_TCP, TCP_NODELAY));
    NOTE("tcp_default_reuseaddr", "%d", optval(t, SOL_SOCKET, SO_REUSEADDR));
    NOTE("tcp_so_type", "%d", optval(t, SOL_SOCKET, SO_TYPE));
    NOTE("tcp_so_error_initial", "%d", optval(t, SOL_SOCKET, SO_ERROR));
    int one = 1;
    CHECK("setsockopt_short_len", setsockopt(t, SOL_SOCKET, SO_REUSEADDR, &one, 1));
    CHECK("setsockopt_bad_level", setsockopt(t, 9999, 1, &one, sizeof one));
    CHECK("getsockopt_bad_name", getsockopt(t, SOL_SOCKET, 99999, &one, &(socklen_t){sizeof one}));
    fcntl(t, F_SETFL, O_NONBLOCK);
    struct sockaddr_in closed_port = loopback(1);
    int crc = connect(t, (struct sockaddr*)&closed_port, sizeof closed_port);
    int cerr = crc ? errno : 0;
    int deferred = crc == -1 && cerr == EINPROGRESS;
    struct pollfd pfd = {t, POLLOUT, 0};
    if (deferred) poll(&pfd, 1, 1000);
    int soerr = deferred ? optval(t, SOL_SOCKET, SO_ERROR) : cerr;
    NOTE("connect_unbound_not_listening_nb", "%s", (crc == -1 && cerr == ECONNREFUSED) || (deferred && soerr == ECONNREFUSED) ? "refused (at once or after poll)" : errname(soerr));
    NOTE("refused_poll_out_when_deferred", "%d", deferred ? !!(pfd.revents & POLLOUT) : 1);
    CHECK("recv_on_unconnected", recv(t, buf, 1, 0));
    close(t);

    int l = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in a = loopback(0);
    CHECK("bind_loopback_port0", bind(l, (struct sockaddr*)&a, sizeof a));
    CHECK("bind_twice", bind(l, (struct sockaddr*)&a, sizeof a));
    socklen_t al = sizeof a;
    getsockname(l, (struct sockaddr*)&a, &al);
    NOTE("getsockname_port_assigned", "%d", ntohs(a.sin_port) != 0);
    CHECK("accept_before_listen", (fcntl(l, F_SETFL, O_NONBLOCK), accept(l, NULL, NULL)));
    CHECK("listen_backlog_negative", listen(l, -1));
    CHECK("accept_nb_empty", accept(l, NULL, NULL));
    int c = socket(AF_INET, SOCK_STREAM, 0);
    CHECK("connect_loopback", connect(c, (struct sockaddr*)&a, sizeof a));
    CHECK("connect_again", connect(c, (struct sockaddr*)&a, sizeof a));
    usleep(50000);
    int s = accept(l, NULL, NULL);
    NOTE("accept_ok", "%d", s >= 0);
    CHECK("accept_inherits_nonblock", fcntl(s, F_GETFL) & O_NONBLOCK);
    CHECK("bind_after_connect", bind(c, (struct sockaddr*)&a, sizeof a));
    CHECK("getpeername_unconnected_listener", getpeername(l, (struct sockaddr*)&a, &al));
    close(s);
    usleep(50000);
    CHECK("recv_after_peer_close", recv(c, buf, sizeof buf, 0));
    close(c);
    close(l);

    int u = socket(AF_INET, SOCK_DGRAM, 0);
    struct sockaddr_in ua = loopback(0);
    bind(u, (struct sockaddr*)&ua, sizeof ua);
    al = sizeof ua;
    getsockname(u, (struct sockaddr*)&ua, &al);
    CHECK("udp_sendto_self", sendto(u, "ping", 4, 0, (struct sockaddr*)&ua, sizeof ua));
    struct pollfd up = {u, POLLIN, 0};
    poll(&up, 1, 1000);
    CHECK("udp_recv_self", recv(u, buf, sizeof buf, MSG_DONTWAIT));
    CHECK("udp_send_unconnected", send(u, "x", 1, 0));
    CHECK("udp_sendto_too_big", sendto(u, buf, 70000, 0, (struct sockaddr*)&ua, sizeof ua));
    close(u);

    struct sockaddr_un sa;
    memset(&sa, 0, sizeof sa);
    sa.sun_family = AF_UNIX;
    char full[sizeof sa.sun_path + 1];
    memset(full, 0, sizeof full);
    strcpy(full, "/tmp/");
    memset(full + 5, 'p', sizeof sa.sun_path - 5);
    memcpy(sa.sun_path, full, sizeof sa.sun_path);
    int ut = socket(AF_UNIX, SOCK_STREAM, 0);
    CHECK("unix_bind_path_unterminated", bind(ut, (struct sockaddr*)&sa, sizeof sa));
    close(ut);
    unlink(full);
    int un = socket(AF_UNIX, SOCK_STREAM, 0);
    memset(sa.sun_path, 0, sizeof sa.sun_path);
    strcpy(sa.sun_path, "/tmp/socket_ops_probe.sock");
    unlink(sa.sun_path);
    CHECK("unix_bind", bind(un, (struct sockaddr*)&sa, SUN_LEN(&sa)));
    int un2 = socket(AF_UNIX, SOCK_STREAM, 0);
    CHECK("unix_bind_in_use", bind(un2, (struct sockaddr*)&sa, SUN_LEN(&sa)));
    CHECK("unix_connect_not_listening", connect(un2, (struct sockaddr*)&sa, SUN_LEN(&sa)));
    close(un2);
    close(un);
    unlink(sa.sun_path);
    return 0;
}
