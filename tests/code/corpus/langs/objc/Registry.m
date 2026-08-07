#import "Shape.h"

@interface Registry : NSObject
- (void)add;
- (void)seed;
@end

@implementation Registry
- (void)add {
}

- (void)seed {
    [self add];
}
@end
