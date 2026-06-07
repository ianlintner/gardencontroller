fn main() {
    let f = rover_core::Frame::new(12345, "r1.0.0", "rover-node-1")
        .with_camera(true, 0)
        .with_net("up", -58, "192.168.1.50", "ok")
        .with_board(1234, 210000, 3_800_000);
    println!("{}", f.to_ndjson());
}
